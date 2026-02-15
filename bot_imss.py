import telebot
import requests
import time
import random
import string
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN ---
# Tu Token oficial de BotFather
TOKEN = "8535232924:AAEnu26jz13UoXk4ccTt0H_bfptX0iqgj84"
bot = telebot.TeleBot(TOKEN)
sesiones = {}

def generar_email_temp():
    """Genera correo aleatorio sin llamar a la API para evitar errores de JSON"""
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp):
    """Busca el link de descarga en la bandeja de 1secmail"""
    user, domain = email_temp.split('@')
    for _ in range(35): # Reintenta por casi 3 minutos
        try:
            url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}"
            res = requests.get(url, timeout=15).json()
            if res:
                msg_id = res[0]['id']
                read_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}"
                msg_data = requests.get(read_url, timeout=15).json()
                soup = BeautifulSoup(msg_data['body'], 'html.parser')
                for a in soup.find_all('a', href=True):
                    if "serviciosdigitales.imss.gob.mx" in a['href']:
                        return a['href']
        except:
            pass
        time.sleep(5)
    return None

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "¡Servidor Arias Online! 🚀\nEnvíame los datos así: CURP NSS")

@bot.message_handler(func=lambda m: len(m.text.split()) == 2)
def iniciar(message):
    chat_id = message.chat.id
    datos = message.text.split()
    curp, nss = datos[0].upper(), datos[1]
    email_temp = generar_email_temp()
    
    bot.send_message(chat_id, f"📧 Correo: {email_temp}\n⏳ Intentando entrar al IMSS (saltando bloqueos)...")

    try:
        pw = sync_playwright().start()
        # Argumento clave: --disable-blink-features=AutomationControlled para no parecer bot
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # Script extra para ocultar que es una automatización
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Carga con tiempo extendido
        page.goto("https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu", 
                  wait_until="domcontentloaded", timeout=90000)
        
        # Esperar a que el campo CURP sea visible
        page.wait_for_selector("#curp", state="visible", timeout=60000)
        
        page.fill("#curp", curp)
        page.fill("#nss", nss)
        page.fill("#correo", email_temp)
        page.fill("#confirmaCorreo", email_temp)

        # Captura de Captcha
        page.wait_for_selector("#captcha_image", timeout=30000)
        path = f"captcha_{chat_id}.png"
        page.locator("#captcha_image").screenshot(path=path)
        
        sesiones[chat_id] = {'page': page, 'browser': browser, 'pw': pw, 'email': email_temp}
        
        with open(path, "rb") as f:
            bot.send_photo(chat_id, f, caption="📸 Resuelve el Captcha para continuar:")
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {str(e)}\n\nReintenta enviando los datos nuevamente.")
        if 'browser' in locals(): browser.close()
        if 'pw' in locals(): pw.stop()

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def finalizar(message):
    chat_id = message.chat.id
    s = sesiones[chat_id]
    try:
        bot.send_message(chat_id, "⚙️ Procesando captcha...")
        s['page'].fill("#captcha", message.text.upper())
        s['page'].click("button:has-text('Continuar')")
        
        bot.send_message(chat_id, "✅ Datos enviados. Buscando link en el correo...")
        link = buscar_link_imss(s['email'])
        if link:
            bot.send_message(chat_id, f"🎯 ¡Éxito! Descarga aquí:\n{link}")
        else:
            bot.send_message(chat_id, "⚠️ No se encontró el correo. Posible captcha incorrecto.")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Fallo al finalizar: {str(e)}")
    finally:
        s['browser'].close()
        s['pw'].stop()
        del sesiones[chat_id]

# Limpiar webhooks y arrancar
bot.remove_webhook()
bot.polling(none_stop=True)
                             
