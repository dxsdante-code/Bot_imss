import telebot
import requests
import time
import random
import string
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN ---
# Tu Token de BotFather
TOKEN = "8535232924:AAEnu26jz13UoXk4ccTt0H_bfptX0iqgj84"
bot = telebot.TeleBot(TOKEN)

# Diccionario para gestionar las sesiones de navegación activas
sesiones = {}

def generar_email_temp():
    """Genera un correo aleatorio localmente para evitar bloqueos de API"""
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp):
    """Vigila la bandeja de entrada del correo temporal en busca del link del IMSS"""
    user, domain = email_temp.split('@')
    # Reintentamos por 3 minutos (el IMSS puede ser lento enviando)
    for _ in range(36): 
        try:
            url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}"
            res = requests.get(url, timeout=15).json()
            if res:
                msg_id = res[0]['id']
                read_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}"
                msg_data = requests.get(read_url, timeout=15).json()
                soup = BeautifulSoup(msg_data['body'], 'html.parser')
                # Buscamos el enlace que contiene el dominio del IMSS
                for a in soup.find_all('a', href=True):
                    if "serviciosdigitales.imss.gob.mx" in a['href']:
                        return a['href']
        except Exception:
            pass 
        time.sleep(5)
    return None

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "¡Servidor Arias Online! 🚀\nEnvíame los datos así: CURP NSS")

@bot.message_handler(func=lambda m: len(m.text.split()) == 2)
def iniciar_tramite(message):
    chat_id = message.chat.id
    datos = message.text.split()
    curp = datos[0].upper()
    nss = datos[1]
    
    email_temp = generar_email_temp()
    bot.send_message(chat_id, f"📧 Correo generado: {email_temp}\n⏳ Conectando al IMSS (esto puede tardar)...")

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        # User Agent robusto para evitar que el IMSS nos detecte como bot
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )
        page = context.new_page()
        
        # Tiempo de espera extendido a 90 segundos para carga inicial
        page.goto("https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu", 
                  wait_until="networkidle", timeout=90000)
        
        # Esperamos a que el formulario sea visible
        page.wait_for_selector("#curp", timeout=60000)
        
        # Rellenar datos
        page.fill("#curp", curp)
        page.fill("#nss", nss)
        page.fill("#correo", email_temp)
        page.fill("#confirmaCorreo", email_temp)

        # Capturar Captcha
        page.wait_for_selector("#captcha_image")
        path_captcha = f"captcha_{chat_id}.png"
        page.locator("#captcha_image").screenshot(path=path_captcha)
        
        # Guardamos la sesión para retomarla al recibir el captcha
        sesiones[chat_id] = {
            'page': page, 
            'browser': browser, 
            'pw': pw, 
            'email': email_temp
        }
        
        with open(path_captcha, "rb") as photo:
            bot.send_photo(chat_id, photo, caption="📸 Escribe los caracteres de la imagen:")
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {str(e)}")
        # Si falla el inicio, cerramos para no dejar procesos colgados
        if 'browser' in locals(): browser.close()
        if 'pw' in locals(): pw.stop()

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def procesar_captcha(message):
    chat_id = message.chat.id
    captcha_texto = message.text.upper()
    s = sesiones[chat_id]
    
    try:
        bot.send_message(chat_id, "⚙️ Validando Captcha...")
        s['page'].fill("#captcha", captcha_texto)
        s['page'].click("button:has-text('Continuar')")
        
        bot.send_message(chat_id, "✅ Datos enviados. Vigilando bandeja de correo...")
        
        # Iniciar búsqueda del link en el correo temporal
        link = buscar_link_imss(s['email'])
        
        if link:
            bot.send_message(chat_id, f"🎉 ¡Éxito! Descarga tu PDF aquí:\n\n{link}")
        else:
            bot.send_message(chat_id, "⚠️ El correo no llegó o el Captcha fue incorrecto.")
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error al finalizar: {str(e)}")
    finally:
        # Limpieza obligatoria de la sesión
        s['browser'].close()
        s['pw'].stop()
        del sesiones[chat_id]

# Limpiar webhooks previos y activar polling
bot.remove_webhook()
bot.polling(none_stop=True)
