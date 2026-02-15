import telebot
import requests
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN ---
TOKEN = "8535232924:AAEnu26jz13UoXk4ccTt0H_bfptX0iqgj84"
bot = telebot.TeleBot(TOKEN)
sesiones = {}

def generar_email_temp():
    res = requests.get("https://www.1secmail.com/api/v1/?action=genEmailDevice&count=1")
    return res.json()[0]

def buscar_link_imss(email_temp):
    user, domain = email_temp.split('@')
    for _ in range(30): # Reintentar por 2.5 minutos
        res = requests.get(f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}").json()
        if res:
            msg_id = res[0]['id']
            msg_data = requests.get(f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}").json()
            soup = BeautifulSoup(msg_data['body'], 'html.parser')
            for a in soup.find_all('a', href=True):
                if "serviciosdigitales.imss.gob.mx" in a['href']:
                    return a['href']
        time.sleep(5)
    return None

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "¡Listo Arias! Envíame: CURP NSS")

@bot.message_handler(func=lambda m: len(m.text.split()) == 2)
def iniciar(message):
    curp, nss = message.text.split()
    email_temp = generar_email_temp()
    bot.send_message(message.chat.id, f"Usando correo temporal: {email_temp}\nAbriendo IMSS...")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu")
    
    page.fill("#curp", curp)
    page.fill("#nss", nss)
    page.fill("#correo", email_temp)
    page.fill("#confirmaCorreo", email_temp)

    # Captura del Captcha
    path = f"captcha_{message.chat.id}.png"
    page.locator("#captcha_image").screenshot(path=path)
    
    sesiones[message.chat.id] = {'page': page, 'browser': browser, 'pw': pw, 'email': email_temp}
    
    with open(path, "rb") as f:
        bot.send_photo(message.chat.id, f, caption="Escribe el Captcha:")

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def finalizar(message):
    s = sesiones[message.chat.id]
    s['page'].fill("#captcha", message.text) # Ajustar ID si es necesario
    s['page'].click("button:has-text('Continuar')") # Ajustar selector
    
    bot.send_message(message.chat.id, "Datos enviados. Esperando correo del IMSS...")
    
    link = buscar_link_imss(s['email'])
    if link:
        bot.send_message(message.chat.id, f"¡Correo recibido! Descarga aquí:\n{link}")
    else:
        bot.send_message(message.chat.id, "No se recibió el correo. Revisa si el Captcha fue correcto.")
    
    s['browser'].close()
    s['pw'].stop()
    del sesiones[message.chat.id]

bot.polling()
  
