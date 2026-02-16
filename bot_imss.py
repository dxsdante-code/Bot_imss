import telebot
import requests
import time
import random
import string
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup

# --- CONFIGURACIÃ“N ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")  # âš ï¸ Usa variable de entorno
bot = telebot.TeleBot(TOKEN)
sesiones = {}

# --- VALIDACIONES ---
def validar_curp(curp):
    """Valida formato de CURP (18 caracteres alfanumÃ©ricos)"""
    patron = r'^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$'
    return bool(re.match(patron, curp.upper()))

def validar_nss(nss):
    """Valida formato de NSS (11 dÃ­gitos)"""
    return nss.isdigit() and len(nss) == 11

# --- FUNCIONES AUXILIARES ---
def generar_email_temp():
    """Genera un correo temporal aleatorio"""
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp, intentos=30, espera=5):
    """
    Busca el link del IMSS en el correo temporal
    
    Args:
        email_temp: Correo temporal generado
        intentos: NÃºmero de intentos (default 30 = 2.5 min)
        espera: Segundos entre intentos
    
    Returns:
        str: URL del documento o None si no se encuentra
    """
    user, domain = email_temp.split('@')
    
    for intento in range(intentos):
        try:
            # Obtener lista de mensajes
            url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}"
            res = requests.get(url, timeout=10).json()
            
            if res and len(res) > 0:
                msg_id = res[0]['id']
                
                # Leer el mensaje
                read_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}"
                msg_data = requests.get(read_url, timeout=10).json()
                
                # Buscar link del IMSS
                soup = BeautifulSoup(msg_data['body'], 'html.parser')
                for a in soup.find_all('a', href=True):
                    if "serviciosdigitales.imss.gob.mx" in a['href']:
                        return a['href']
        
        except Exception as e:
            print(f"Error en intento {intento + 1}: {e}")
        
        time.sleep(espera)
    
    return None

def limpiar_captcha(chat_id):
    """Elimina el archivo de captcha temporal"""
    path = f"captcha_{chat_id}.png"
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"Error al limpiar captcha: {e}")

def cerrar_sesion(chat_id):
    """Cierra y limpia una sesiÃ³n de manera segura"""
    if chat_id in sesiones:
        s = sesiones[chat_id]
        try:
            if 'page' in s and s['page']:
                s['page'].close()
            if 'browser' in s and s['browser']:
                s['browser'].close()
            if 'pw' in s and s['pw']:
                s['pw'].stop()
        except Exception as e:
            print(f"Error al cerrar sesiÃ³n: {e}")
        finally:
            del sesiones[chat_id]
            limpiar_captcha(chat_id)

# --- COMANDOS DEL BOT ---
@bot.message_handler(commands=['start', 'ayuda'])
def start(message):
    """Comando de inicio"""
    texto = """
ðŸ¥ *Bot IMSS - Semanas Cotizadas*

ðŸ“‹ *Formato:*
`CURP NSS`

ðŸ“ *Ejemplo:*
`AAAA850101HDFBBB09 12345678901`

âš ï¸ *Importante:*
â€¢ CURP: 18 caracteres
â€¢ NSS: 11 dÃ­gitos
â€¢ Sin espacios extras

ðŸ’¡ Usa /cancelar para detener el proceso
    """
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['cancelar'])
def cancelar(message):
    """Cancela el proceso actual"""
    chat_id = message.chat.id
    if chat_id in sesiones:
        cerrar_sesion(chat_id)
        bot.reply_to(message, "âŒ Proceso cancelado.")
    else:
        bot.reply_to(message, "No hay ningÃºn proceso activo.")

@bot.message_handler(func=lambda m: len(m.text.split()) == 2 and m.chat.id not in sesiones)
def iniciar_consulta(message):
    """Inicia el proceso de consulta al IMSS"""
    chat_id = message.chat.id
    datos = message.text.split()
    curp, nss = datos[0].upper(), datos[1]
    
    # Validaciones
    if not validar_curp(curp):
        bot.reply_to(message, "âŒ CURP invÃ¡lida. Debe tener 18 caracteres.\nEjemplo: AAAA850101HDFBBB09")
        return
    
    if not validar_nss(nss):
        bot.reply_to(message, "âŒ NSS invÃ¡lido. Debe tener 11 dÃ­gitos.\nEjemplo: 12345678901")
        return
    
    email_temp = generar_email_temp()
    
    msg_progreso = bot.send_message(
        chat_id, 
        f"ðŸ“§ *Correo temporal:* `{email_temp}`\n"
        f"â³ Conectando con el portal del IMSS...\n\n"
        f"_Esto puede tardar 30-60 segundos_",
        parse_mode="Markdown"
    )

    pw = None
    browser = None
    page = None

    try:
        # Inicializar Playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage'
            ]
        )
        
        # Crear contexto con user agent real
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        
        # Ocultar automatizaciÃ³n
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        # Navegar al portal
        bot.edit_message_text(
            "ðŸŒ Cargando portal del IMSS...",
            chat_id,
            msg_progreso.message_id
        )
        
        page.goto(
            "https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu",
            wait_until="networkidle",
            timeout=60000
        )
        
        # Esperar formulario
        bot.edit_message_text(
            "ðŸ“ Llenando formulario...",
            chat_id,
            msg_progreso.message_id
        )
        
        page.wait_for_selector("#curp", timeout=30000)
        
        # Llenar formulario con delays humanos
        page.type("#curp", curp, delay=random.randint(80, 150))
        time.sleep(0.5)
        
        page.type("#nss", nss, delay=random.randint(80, 150))
        time.sleep(0.5)
        
        page.type("#correo", email_temp, delay=random.randint(80, 150))
        time.sleep(0.5)
        
        page.type("#confirmaCorreo", email_temp, delay=random.randint(80, 150))
        time.sleep(1)

        # Esperar y capturar captcha
        bot.edit_message_text(
            "ðŸ“¸ Capturando CAPTCHA...",
            chat_id,
            msg_progreso.message_id
        )
        
        page.wait_for_selector("#captcha_image", timeout=20000)
        captcha_path = f"captcha_{chat_id}.png"
        page.locator("#captcha_image").screenshot(path=captcha_path)
        
        # Guardar sesiÃ³n
        sesiones[chat_id] = {
            'page': page,
            'browser': browser,
            'pw': pw,
            'email': email_temp,
            'curp': curp,
            'nss': nss
        }
        
        # Enviar captcha
        with open(captcha_path, "rb") as f:
            bot.delete_message(chat_id, msg_progreso.message_id)
            bot.send_photo(
                chat_id, 
                f, 
                caption="ðŸ“¸ *Escribe el CAPTCHA*\n\n"
                        "â±ï¸ Tienes 5 minutos para responder\n"
                        "ðŸ’¡ Usa /cancelar para detener",
                parse_mode="Markdown"
            )
            
    except PlaywrightTimeout:
        bot.edit_message_text(
            "â±ï¸ Tiempo de espera agotado. El portal del IMSS no responde.\n"
            "Por favor, intenta nuevamente en unos minutos.",
            chat_id,
            msg_progreso.message_id
        )
        cerrar_sesion(chat_id)
        
    except Exception as e:
        bot.edit_message_text(
            f"âŒ Error inesperado: {str(e)[:100]}\n\n"
            f"Intenta nuevamente con /start",
            chat_id,
            msg_progreso.message_id
        )
        cerrar_sesion(chat_id)

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def procesar_captcha(message):
    """Procesa el captcha y completa la consulta"""
    chat_id = message.chat.id
    captcha_text = message.text.strip().upper()
    
    if not captcha_text:
        bot.reply_to(message, "âš ï¸ El CAPTCHA no puede estar vacÃ­o. Intenta de nuevo.")
        return
    
    s = sesiones[chat_id]
    msg_progreso = bot.send_message(chat_id, "â³ Validando CAPTCHA...")
    
    try:
        # Escribir captcha
        s['page'].type("#captcha", captcha_text, delay=random.randint(100, 200))
        time.sleep(0.5)
        
        # Hacer clic en continuar
        bot.edit_message_text("ðŸ”„ Enviando formulario...", chat_id, msg_progreso.message_id)
        s['page'].click("button[type='submit']", timeout=10000)
        
        # Esperar respuesta
        time.sleep(3)
        
        # Buscar link en el correo
        bot.edit_message_text(
            "ðŸ“¬ Buscando correo del IMSS...\n_Esto puede tardar 1-2 minutos_",
            chat_id,
            msg_progreso.message_id,
            parse_mode="Markdown"
        )
        
        link = buscar_link_imss(s['email'])
        
        if link:
            bot.edit_message_text(
                f"âœ… *Â¡Ã‰xito!*\n\n"
                f"ðŸ“„ Descarga tu constancia aquÃ­:\n{link}\n\n"
                f"_El enlace expira en 24 horas_",
                chat_id,
                msg_progreso.message_id,
                parse_mode="Markdown"
            )
        else:
            # Verificar si hubo error en el portal
            try:
                error_msg = s['page'].locator(".error-message, .alert-danger").text_content(timeout=3000)
                bot.edit_message_text(
                    f"âš ï¸ *Error del portal IMSS:*\n{error_msg}\n\n"
                    f"Verifica tus datos e intenta nuevamente.",
                    chat_id,
                    msg_progreso.message_id,
                    parse_mode="Markdown"
                )
            except:
                bot.edit_message_text(
                    "âš ï¸ No se recibiÃ³ el correo del IMSS.\n\n"
                    "*Posibles causas:*\n"
                    "â€¢ CAPTCHA incorrecto\n"
                    "â€¢ Datos incorrectos (CURP/NSS)\n"
                    "â€¢ El portal estÃ¡ saturado\n\n"
                    "Intenta nuevamente con /start",
                    chat_id,
                    msg_progreso.message_id,
                    parse_mode="Markdown"
                )
    
    except Exception as e:
        bot.edit_message_text(
            f"âŒ Error al procesar: {str(e)[:150]}\n\n"
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
    if message.chat.id in sesiones:
        bot.reply_to(message, "âš ï¸ Esperando el CAPTCHA. Si deseas cancelar, usa /cancelar")
    else:
        bot.reply_to(message, "âŒ Formato incorrecto. Usa /start para ver las instrucciones.")

# --- INICIO DEL BOT ---
if __name__ == "__main__":
    print("ðŸ¤– Bot iniciando...")
    
    # Limpiar webhook previo (evita error 409)
    try:
        bot.remove_webhook()
        time.sleep(1)
    except:
        pass
    
    print("âœ… Bot en lÃ­nea")
    bot.polling(none_stop=True, interval=1, timeout=60)
