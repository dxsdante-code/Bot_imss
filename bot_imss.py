import telebot
import requests
import time
import random
import string
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN ---
TOKEN = "8535232924:AAEnu26jz13UoXk4ccTt0H_bfptX0iqgj84"
bot = telebot.TeleBot(TOKEN)
sesiones = {}

def generar_email_temp():
    # Genera el nombre del correo localmente para evitar el error de JSON que vimos en tus logs
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp):
    user, domain = email_temp.split('@')
    for _ in range(30): # Reintenta por 2.5 minutos
        try:
            url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}"
            res = requests.get(url, timeout=10).json()
            if res:
                msg_id = res[0]['id']
                read_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}"
                msg_data = requests.get(read_url, timeout=10).json()
                soup = BeautifulSoup(msg_data['body'], 'html.parser')
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
def iniciar(message):
    chat_id = message.chat.id
    datos = message.text.split()
    curp, nss = datos[0].upper(), datos[1]
    
    email_temp = generar_email_temp()
    bot.send_message(chat_id, f"📧 Correo generado: {email_temp}\n⏳ Conectando al IMSS...")

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        # Usamos un User-Agent de celular para evitar bloqueos del IMSS
        context = browser.new_context(user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36")
        page = context.new_page()
        
        page.goto("https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu", timeout=60000)
        page.fill("#curp", curp)
        page.fill("#nss", nss)
        page.fill("#correo", email_temp)
        page.fill("#confirmaCorreo", email_temp)

        # Captura del Captcha
        page.wait_for_selector("#captcha_image")
        path = f"captcha_{chat_id}.png"
        page.locator("#captcha_image").screenshot(path=path)
        
        # Guardamos la sesión activa en el servidor
        sesiones[chat_id] = {'page': page, 'browser': browser, 'pw': pw, 'email': email_temp}
        
        with open(path, "rb") as f:
            bot.send_photo(chat_id, f, caption="Escribe los caracteres que ves en la imagen:")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error de conexión: {str(e)}")

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def finalizar(message):
    chat_id = message.chat.id
    s = sesiones[chat_id]
    try:
        s['page'].fill("#captcha", message.text.upper())
        s['page'].click("button:has-text('Continuar')")
        bot.send_message(chat_id, "✅ Procesando... Esperando el correo del IMSS.")
        
        link = buscar_link_imss(s['email'])
        if link:
            bot.send_message(chat_id, f"🎯 ¡Listo! Aquí está tu link:\n{link}")
        else:
            bot.send_message(chat_id, "⚠️ El IMSS no envió el correo o el captcha fue incorrecto.")
    finally:
        s['browser'].close()
        s['pw'].stop()
        del sesiones[chat_id]

# Esto soluciona el error 409 de tus logs al reiniciar la conexión
bot.remove_webhook()
bot.polling(none_stop=True)
