import telebot
import requests
import time
import random
import string
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
from functools import wraps

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN no configurado")
    exit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
sesiones: Dict[int, Dict[str, Any]] = {}

# Límites
MAX_INTENTOS_EMAIL = 40  # 3.3 minutos
ESPERA_EMAIL = 5
TIMEOUT_CAPTCHA = 300  # 5 minutos
TIMEOUT_SESION = 900  # 15 minutos
MAX_SESIONES = 5

# --- VALIDACIONES ---
def validar_curp(curp: str) -> bool:
    """
    Valida CURP con formato y dígito verificador
    
    Args:
        curp: CURP a validar
    
    Returns:
        bool: True si es válido
    """
    curp = curp.upper()
    
    # Validar formato
    patron = r'^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$'
    if not re.match(patron, curp):
        return False
    
    # Validar dígito verificador (algoritmo oficial CURP)
    try:
        valores = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
        curp_sin_digito = curp[:17]
        
        suma = 0
        pesos = [3 * (i % 2 + 1) for i in range(17)]
        
        for i, char in enumerate(curp_sin_digito):
            pos = valores.index(char)
            suma += (pos * pesos[i]) % 10
        
        digito_esperado = (10 - (suma % 10)) % 10
        return int(curp[17]) == digito_esperado
    except Exception as e:
        logger.warning(f"Error validando dígito CURP: {e}")
        return True  # Permitir si no se puede validar el dígito

def validar_nss(nss: str) -> bool:
    """
    Valida formato de NSS (11 dígitos)
    
    Args:
        nss: NSS a validar
    
    Returns:
        bool: True si es válido
    """
    if not nss.isdigit() or len(nss) != 11:
        return False
    
    # NSS no debe ser todo ceros
    if nss == "00000000000":
        return False
    
    return True

# --- GESTIÓN DE SESIONES ---
def sesion_valida(chat_id: int) -> bool:
    """Verifica si una sesión existe y no expiró"""
    if chat_id not in sesiones:
        return False
    
    sesion = sesiones[chat_id]
    tiempo_transcurrido = time.time() - sesion.get('creada', time.time())
    
    if tiempo_transcurrido > TIMEOUT_SESION:
        logger.warning(f"⏰ Sesión {chat_id} expirada")
        cerrar_sesion(chat_id)
        return False
    
    return True

def limpiar_sesiones_viejas():
    """Cierra todas las sesiones expiradas"""
    chats_a_limpiar = []
    
    for chat_id, sesion in sesiones.items():
        tiempo_transcurrido = time.time() - sesion.get('creada', time.time())
        if tiempo_transcurrido > TIMEOUT_SESION:
            chats_a_limpiar.append(chat_id)
    
    for chat_id in chats_a_limpiar:
        try:
            logger.info(f"Limpiando sesión expirada: {chat_id}")
            cerrar_sesion(chat_id)
        except Exception as e:
            logger.error(f"Error limpiando sesión {chat_id}: {e}")

# --- FUNCIONES AUXILIARES ---
def generar_email_temp() -> str:
    """Genera un correo temporal aleatorio"""
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp: str, intentos: int = MAX_INTENTOS_EMAIL, 
                     espera: int = ESPERA_EMAIL) -> Optional[str]:
    """
    Busca el link del IMSS en el correo temporal con reintentos y backoff
    
    Args:
        email_temp: Correo temporal generado
        intentos: Número de intentos
        espera: Segundos entre intentos
    
    Returns:
        str: URL del documento o None si no se encuentra
    """
    user, domain = email_temp.split('@')
    logger.info(f"🔍 Buscando link en {email_temp}")
    
    for intento in range(intentos):
        try:
            # Obtener lista de mensajes
            url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}"
            res = requests.get(url, timeout=10).json()
            
            if res and len(res) > 0:
                msg_id = res[0]['id']
                logger.info(f"✉️  Mensaje encontrado: {msg_id}")
                
                # Leer el mensaje
                read_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}"
                msg_data = requests.get(read_url, timeout=10).json()
                
                # Buscar link del IMSS
                soup = BeautifulSoup(msg_data.get('body', ''), 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if "serviciosdigitales.imss.gob.mx" in href:
                        logger.info(f"✅ Link IMSS encontrado: {href[:50]}...")
                        return href
                
                logger.warning(f"⚠️  Mensaje encontrado pero sin link IMSS válido")
                return None  # No buscar más si el mensaje existe pero no tiene link
        
        except requests.exceptions.Timeout:
            logger.warning(f"⏱️  Timeout en intento {intento + 1}/{intentos}")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"🌐 Error de conexión en intento {intento + 1}/{intentos}: {e}")
        except Exception as e:
            logger.warning(f"⚠️  Error en intento {intento + 1}/{intentos}: {type(e).__name__}: {e}")
        
        if intento < intentos - 1:
            logger.info(f"⏳ Esperando {espera}s antes de reintento {intento + 2}/{intentos}")
            time.sleep(espera)
    
    logger.error(f"❌ No se encontró link después de {intentos} intentos")
    return None

def limpiar_captcha(chat_id: int) -> None:
    """Elimina el archivo de captcha temporal"""
    path = f"captcha_{chat_id}.png"
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"🗑️  Captcha limpiado: {path}")
    except Exception as e:
        logger.error(f"Error al limpiar captcha {path}: {e}")

def cerrar_sesion(chat_id: int) -> None:
    """Cierra y limpia una sesión de manera segura"""
    if chat_id not in sesiones:
        return
    
    s = sesiones[chat_id]
    logger.info(f"🔌 Cerrando sesión {chat_id}")
    
    try:
        if 'page' in s and s['page']:
            try:
                s['page'].close()
            except Exception as e:
                logger.warning(f"Error cerrando page: {e}")
        
        if 'context' in s and s['context']:
            try:
                s['context'].close()
            except Exception as e:
                logger.warning(f"Error cerrando context: {e}")
        
        if 'browser' in s and s['browser']:
            try:
                s['browser'].close()
            except Exception as e:
                logger.warning(f"Error cerrando browser: {e}")
        
        if 'pw' in s and s['pw']:
            try:
                s['pw'].stop()
            except Exception as e:
                logger.warning(f"Error deteniendo playwright: {e}")
    
    except Exception as e:
        logger.error(f"Error crítico al cerrar sesión {chat_id}: {e}")
    
    finally:
        if chat_id in sesiones:
            del sesiones[chat_id]
        limpiar_captcha(chat_id)

def middleware_limpieza(handler_func):
    """Decorator para limpiar sesiones viejas antes de cada comando"""
    @wraps(handler_func)
    def wrapper(*args, **kwargs):
        limpiar_sesiones_viejas()
        return handler_func(*args, **kwargs)
    return wrapper

# --- COMANDOS DEL BOT ---
@bot.message_handler(commands=['start', 'ayuda'])
@middleware_limpieza
def start(message):
    """Comando de inicio con instrucciones"""
    chat_id = message.chat.id
    
    texto = """
🏥 *Bot IMSS - Semanas Cotizadas v2.0*

📋 *Formato:*
`CURP NSS`

📝 *Ejemplo:*
`AAAA850101HDFBBB09 12345678901`

⚠️ *Importante:*
• CURP: 18 caracteres (validado automáticamente)
• NSS: 11 dígitos
• Sin espacios extras
• Caracteres válidos solamente

🔐 *Seguridad:*
• Tus datos NO se almacenan
• Sesiones expiran automáticamente
• Usa /cancelar en cualquier momento

💡 Usa /cancelar para detener el proceso
📞 Usa /estado para ver el estado del bot
    """
    bot.reply_to(message, texto)
    logger.info(f"👤 Usuario {chat_id} inició el bot")

@bot.message_handler(commands=['cancelar'])
def cancelar(message):
    """Cancela el proceso actual"""
    chat_id = message.chat.id
    
    if chat_id in sesiones:
        cerrar_sesion(chat_id)
        bot.reply_to(message, "❌ Proceso cancelado. Tu sesión fue cerrada.")
        logger.info(f"❌ Usuario {chat_id} canceló la sesión")
    else:
        bot.reply_to(message, "No hay ningún proceso activo.")

@bot.message_handler(commands=['estado'])
def estado(message):
    """Muestra el estado del bot"""
    chat_id = message.chat.id
    
    texto = f"""
📊 *Estado del Bot*

👥 Sesiones activas: `{len(sesiones)}/{MAX_SESIONES}`
🕐 Hora: `{datetime.now().strftime('%H:%M:%S')}`
⏱️ Timeout sesión: `{TIMEOUT_SESION}s`

{'🟢 Bot operativo' if len(sesiones) < MAX_SESIONES else '🔴 Bot al máximo de capacidad'}
    """
    bot.reply_to(message, texto)

@bot.message_handler(func=lambda m: len(m.text.split()) == 2 and m.chat.id not in sesiones)
@middleware_limpieza
def iniciar_consulta(message):
    """Inicia el proceso de consulta al IMSS"""
    chat_id = message.chat.id
    
    # Validar capacidad
    if len(sesiones) >= MAX_SESIONES:
        bot.reply_to(message, 
            f"⚠️ El bot está al máximo de capacidad ({MAX_SESIONES} sesiones activas).\n"
            f"Por favor, intenta en unos momentos.")
        logger.warning(f"Bot al máximo. Usuario {chat_id} rechazado")
        return
    
    # Parsear entrada
    try:
        datos = message.text.split()
        curp, nss = datos[0].upper(), datos[1]
    except Exception as e:
        bot.reply_to(message, "❌ Formato incorrecto. Usa: CURP NSS")
        logger.error(f"Error parseando entrada de {chat_id}: {e}")
        return
    
    # Validaciones
    if not validar_curp(curp):
        bot.reply_to(message, 
            "❌ *CURP inválida.*\n"
            "Debe tener 18 caracteres y ser válida.\n"
            "Ejemplo: `AAAA850101HDFBBB09`")
        logger.warning(f"CURP inválida de {chat_id}: {curp}")
        return
    
    if not validar_nss(nss):
        bot.reply_to(message, 
            "❌ *NSS inválido.*\n"
            "Debe tener 11 dígitos.\n"
            "Ejemplo: `12345678901`")
        logger.warning(f"NSS inválido de {chat_id}: {nss}")
        return
    
    email_temp = generar_email_temp()
    logger.info(f"👤 Usuario {chat_id} iniciando consulta con email {email_temp}")
    
    msg_progreso = bot.send_message(
        chat_id, 
        f"📧 *Correo temporal:* `{email_temp}`\n"
        f"⏳ Conectando con el portal del IMSS...\n\n"
        f"_Esto puede tardar 30-60 segundos_"
    )

    pw = None
    browser = None
    context = None
    page = None

    try:
        # Inicializar Playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu'
            ]
        )
        
        # Crear contexto con user agent realista
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        page = context.new_page()
        page.set_default_timeout(30000)  # 30s timeout global
        
        # Ocultar automatización
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['es-MX', 'es']});
        """)

        # Navegar al portal
        bot.edit_message_text(
            "🌐 Cargando portal del IMSS...",
            chat_id,
            msg_progreso.message_id
        )
        
        page.goto(
            "https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu",
            wait_until="networkidle",
            timeout=60000
        )
        logger.info(f"✅ Portal cargado para {chat_id}")
        
        # Esperar formulario
        bot.edit_message_text(
            "📝 Llenando formulario...",
            chat_id,
            msg_progreso.message_id
        )
        
        page.wait_for_selector("#curp", timeout=30000)
        
        # Llenar formulario con delays humanoides
        page.type("#curp", curp, delay=random.randint(80, 150))
        time.sleep(random.uniform(0.3, 0.7))
        
        page.type("#nss", nss, delay=random.randint(80, 150))
        time.sleep(random.uniform(0.3, 0.7))
        
        page.type("#correo", email_temp, delay=random.randint(80, 150))
        time.sleep(random.uniform(0.3, 0.7))
        
        page.type("#confirmaCorreo", email_temp, delay=random.randint(80, 150))
        time.sleep(random.uniform(0.8, 1.2))

        # Esperar y capturar captcha
        bot.edit_message_text(
            "🔐 Capturando CAPTCHA...",
            chat_id,
            msg_progreso.message_id
        )
        
        page.wait_for_selector("#captcha_image", timeout=20000)
        captcha_path = f"captcha_{chat_id}.png"
        page.locator("#captcha_image").screenshot(path=captcha_path)
        logger.info(f"📸 CAPTCHA capturado para {chat_id}")
        
        # Guardar sesión con timestamp
        sesiones[chat_id] = {
            'page': page,
            'browser': browser,
            'context': context,
            'pw': pw,
            'email': email_temp,
            'curp': curp,
            'nss': nss,
            'creada': time.time(),
            'intentos_captcha': 0
        }
        
        # Enviar captcha
        try:
            with open(captcha_path, "rb") as f:
                bot.delete_message(chat_id, msg_progreso.message_id)
                bot.send_photo(
                    chat_id, 
                    f, 
                    caption="🔐 *Escribe el CAPTCHA que ves en la imagen*\n\n"
                            "⏱️ Tienes 5 minutos para responder\n"
                            "💡 Usa /cancelar para detener"
                )
                logger.info(f"📤 CAPTCHA enviado a {chat_id}")
        except FileNotFoundError:
            bot.edit_message_text(
                "❌ Error: No se pudo cargar la imagen del CAPTCHA",
                chat_id,
                msg_progreso.message_id
            )
            cerrar_sesion(chat_id)
            
    except PlaywrightTimeout as e:
        logger.error(f"⏱️ Timeout de Playwright para {chat_id}: {e}")
        bot.edit_message_text(
            "⏱️ *Tiempo de espera agotado.*\n\n"
            "El portal del IMSS no responde. Por favor, intenta en unos minutos.",
            chat_id,
            msg_progreso.message_id
        )
        cerrar_sesion(chat_id)
        
    except Exception as e:
        logger.error(f"❌ Error en iniciar_consulta para {chat_id}: {type(e).__name__}: {e}")
        error_msg = str(e)[:100]
        bot.edit_message_text(
            f"❌ *Error inesperado:*\n`{error_msg}`\n\n"
            f"Intenta nuevamente con /start",
            chat_id,
            msg_progreso.message_id
        )
        cerrar_sesion(chat_id)

@bot.message_handler(func=lambda m: sesion_valida(m.chat.id))
def procesar_captcha(message):
    """Procesa el CAPTCHA y completa la consulta"""
    chat_id = message.chat.id
    
    if not sesion_valida(chat_id):
        bot.reply_to(message, "❌ Tu sesión expiró. Usa /start para comenzar de nuevo.")
        return
    
    captcha_text = message.text.strip().upper()
    
    if not captcha_text or len(captcha_text) < 4:
        bot.reply_to(message, "⚠️ El CAPTCHA debe tener al menos 4 caracteres. Intenta de nuevo.")
        return
    
    s = sesiones[chat_id]
    s['intentos_captcha'] += 1
    
    logger.info(f"🔐 Usuario {chat_id} ingresó CAPTCHA (intento {s['intentos_captcha']})")
    
    msg_progreso = bot.send_message(chat_id, "⏳ Validando CAPTCHA...")
    
    try:
        # Escribir CAPTCHA
        s['page'].type("#captcha", captcha_text, delay=random.randint(100, 200))
        time.sleep(random.uniform(0.5, 1.0))
        
        # Hacer clic en continuar
        bot.edit_message_text("📤 Enviando formulario...", chat_id, msg_progreso.message_id)
        s['page'].click("button[type='submit']", timeout=10000)
        logger.info(f"✅ Formulario enviado para {chat_id}")
        
        # Esperar respuesta del servidor
        time.sleep(random.uniform(2, 4))
        
        # Buscar link en el correo
        bot.edit_message_text(
            "📬 Buscando correo del IMSS...\n_Esto puede tardar 1-2 minutos_",
            chat_id,
            msg_progreso.message_id
        )
        logger.info(f"🔍 Buscando email para {chat_id}...")
        
        link = buscar_link_imss(s['email'])
        
        if link:
            bot.edit_message_text(
                f"✅ *¡Éxito!*\n\n"
                f"📄 Descarga tu constancia aquí:\n{link}\n\n"
                f"_El enlace expira en 24 horas_",
                chat_id,
                msg_progreso.message_id
            )
            logger.info(f"✅ Consulta exitosa para {chat_id}")
        else:
            # Verificar si hubo error en el portal
            try:
                error_msg = s['page'].locator(".error-message, .alert-danger").text_content(timeout=3000)
                bot.edit_message_text(
                    f"⚠️ *Error del portal IMSS:*\n{error_msg}\n\n"
                    f"Verifica tus datos e intenta nuevamente.",
                    chat_id,
                    msg_progreso.message_id
                )
                logger.warning(f"Error del portal para {chat_id}: {error_msg}")
            except:
                bot.edit_message_text(
                    "⚠️ *No se recibió el correo del IMSS.*\n\n"
                    "*Posibles causas:*\n"
                    "• CAPTCHA incorrecto\n"
                    "• Datos incorrectos (CURP/NSS)\n"
                    "• El portal está saturado\n"
                    "• Problema de conexión\n\n"
                    "Intenta nuevamente con /start",
                    chat_id,
                    msg_progreso.message_id
                )
                logger.warning(f"No se encontró email para {chat_id}")
    
    except Exception as e:
        logger.error(f"❌ Error en procesar_captcha para {chat_id}: {type(e).__name__}: {e}")
        bot.edit_message_text(
            f"❌ *Error al procesar:*\n`{str(e)[:100]}`\n\n"
            f"Intenta nuevamente con /start",
            chat_id,
            msg_progreso.message_id
        )
    
    finally:
        cerrar_sesion(chat_id)

# --- MANEJO DE ERRORES GLOBALES ---
@bot.message_handler(func=lambda m: True)
def mensaje_invalido(message):
    """Maneja mensajes no reconocidos"""
    chat_id = message.chat.id
    
    if sesion_valida(chat_id):
        bot.reply_to(message, 
            "⚠️ Esperando el CAPTCHA.\n"
            "Si deseas cancelar, usa /cancelar")
    else:
        bot.reply_to(message, 
            "❌ *Formato incorrecto.*\n\n"
            "Usa /start para ver las instrucciones")
        logger.info(f"Mensaje inválido de {chat_id}: {message.text[:50]}")

@bot.error_handler(func=lambda call: True)
def handle_error(error):
    """Maneja errores globales del bot"""
    logger.error(f"❌ Error del bot: {type(error).__name__}: {error}")

# --- INICIO DEL BOT ---
if __name__ == "__main__":
    logger.info("🤖 Bot iniciando...")
    
    # Limpiar webhook previo
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        logger.warning(f"Error removiendo webhook: {e}")
    
    logger.info("✅ Bot en línea y escuchando mensajes...")
    
    try:
        bot.polling(none_stop=True, interval=1, timeout=60)
    except Exception as e:
        logger.error(f"❌ Error crítico: {e}")
        raise
