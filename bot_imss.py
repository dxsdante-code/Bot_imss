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
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp):
    user, domain = email_temp.split('@')
    for _ in range(30):
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
        except: pass
        time.sleep(5)
    return None

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "¡Listo Arias! Envíame: CURP NSS")

@bot.message_handler(func=lambda m: len(m.text.split()) == 2)
def iniciar(message):
    chat_id = message.chat.id
    datos = message.text.split()
    curp, nss = datos[0].upper(), datos[1]
    email_temp = generar_email_temp()
    
    bot.send_message(chat_id, f"📧 Correo: {email_temp}\n⏳ Conectando al IMSS (saltando bloqueos)...")

    try:
        pw = sync_playwright().start()
        # Argumentos para evitar detección de servidor
        browser = pw.chromium.launch(headless=True, args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-setuid-sandbox'
        ])
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1366, 'height': 768}
        )
        
        page = context.new_page()
        
        # Engañar al sitio para que no vea que somos una automatización
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # Cargar la página esperando a que el portal responda
        page.goto("https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu", 
                  wait_until="commit", timeout=100000)
        
        # Esperar específicamente a que el cuadro esté listo para escribir
        page.wait_for_selector("#curp", state="attached", timeout=60000)
        time.sleep(2) # Pausa humana
        
        page.fill("#curp", curp)
        page.fill("#nss", nss)
        page.fill("#correo", email_temp)
        page.fill("#confirmaCorreo", email_temp)

        page.wait_for_selector("#captcha_image", timeout=30000)
        path = f"captcha_{chat_id}.png"
        page.locator("#captcha_image").screenshot(path=path)
        
        sesiones[chat_id] = {'page': page, 'browser': browser, 'pw': pw, 'email': email_temp}
        
        with open(path, "rb") as f:
            bot.send_photo(chat_id, f, caption="📸 Resuelve el Captcha:")
            
    except Exception as e:
        bot.send_message(chat_id, "⚠️ El portal del IMSS está saturado o bloqueando la conexión. Intenta de nuevo en un momento.")
        print(f"Error detallado: {e}")
        if 'browser' in locals(): browser.close()
        if 'pw' in locals(): pw.stop()

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def finalizar(message):
    chat_id = message.chat.id
    s = sesiones[chat_id]
    try:
        bot.send_message(chat_id, "⚙️ Validando...")
        s['page'].fill("#captcha", message.text.upper())
        s['page'].click("button:has-text('Continuar')")
        
        bot.send_message(chat_id, "✅ Buscando link en el correo...")
        link = buscar_link_imss(s['email'])
        if link:
            bot.send_message(chat_id, f"🎯 ¡Éxito! Descarga aquí:\n{link}")
        else:
            bot.send_message(chat_id, "⚠️ El correo no llegó. Revisa si el captcha fue correcto.")
    finally:
        s['browser'].close()
        s['pw'].stop()
        del sesiones[chat_id]

bot.remove_webhook()
bot.polling(none_stop=True)
        
