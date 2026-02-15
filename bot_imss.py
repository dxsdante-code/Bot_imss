import telebot
import requests
import time
import random
import string
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN ---
# Tu Token real extraído de BotFather
TOKEN = "8535232924:AAEnu26jz13UoXk4ccTt0H_bfptX0iqgj84"
bot = telebot.TeleBot(TOKEN)

# Diccionario para gestionar las sesiones activas en el servidor
sesiones = {}

def generar_email_temp():
    """Genera un correo aleatorio sin consultar la API inicialmente para evitar bloqueos"""
    nombre = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{nombre}@1secmail.com"

def buscar_link_imss(email_temp):
    """Vigila la bandeja de entrada del correo temporal"""
    user, domain = email_temp.split('@')
    # Reintentar durante 3 minutos (el IMSS a veces tarda)
    for _ in range(36): 
        try:
            url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={user}&domain={domain}"
            res = requests.get(url).json()
            if res:
                msg_id = res[0]['id']
                # Leer el contenido del mensaje encontrado
                read_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={user}&domain={domain}&id={msg_id}"
                msg_data = requests.get(read_url).json()
                soup = BeautifulSoup(msg_data['body'], 'html.parser')
                # Buscar el enlace que contiene la dirección del IMSS
                for a in soup.find_all('a', href=True):
                    if "serviciosdigitales.imss.gob.mx" in a['href']:
                        return a['href']
        except:
            pass 
        time.sleep(5)
    return None

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "¡Servidor Listo Arias! 🚀\nEnvíame los datos así: CURP NSS")

@bot.message_handler(func=lambda m: len(m.text.split()) == 2)
def iniciar_tramite(message):
    chat_id = message.chat.id
    datos = message.text.split()
    curp = datos[0].upper()
    nss = datos[1]
    
    email_temp = generar_email_temp()
    bot.send_message(chat_id, f"📧 Correo generado: {email_temp}\n⏳ Abriendo portal del IMSS...")

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
        page = context.new_page()
        
        page.goto("https://serviciosdigitales.imss.gob.mx/semanascotizadas-web/usuarios/IngresoMenu", timeout=60000)
        
        # Rellenar el formulario
        page.fill("#curp", curp)
        page.fill("#nss", nss)
        page.fill("#correo", email_temp)
        page.fill("#confirmaCorreo", email_temp)

        # Capturar la imagen del Captcha
        path_captcha = f"captcha_{chat_id}.png"
        page.wait_for_selector("#captcha_image")
        page.locator("#captcha_image").screenshot(path=path_captcha)
        
        # Guardamos la sesión abierta para continuar tras la respuesta del usuario
        sesiones[chat_id] = {
            'page': page, 
            'browser': browser, 
            'pw': pw, 
            'email': email_temp,
            'curp': curp
        }
        
        with open(path_captcha, "rb") as photo:
            bot.send_photo(chat_id, photo, caption="📸 Escribe los caracteres de la imagen:")
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error al conectar con el IMSS: {str(e)}")

@bot.message_handler(func=lambda m: m.chat.id in sesiones)
def procesar_captcha(message):
    chat_id = message.chat.id
    captcha_texto = message.text.upper()
    s = sesiones[chat_id]
    
    try:
        bot.send_message(chat_id, "⚙️ Enviando Captcha...")
        s['page'].fill("#captcha", captcha_texto)
        # Click en el botón de continuar (usamos selector por texto para mayor precisión)
        s['page'].click("button:has-text('Continuar')")
        
        bot.send_message(chat_id, "✅ Datos enviados correctamente. Esperando que el IMSS envíe el correo...")
        
        # Iniciar vigilancia del correo
        link_descarga = buscar_link_imss(s['email'])
        
        if link_descarga:
            bot.send_message(chat_id, f"🎉 ¡Éxito! Aquí tienes tu link de descarga:\n\n{link_descarga}")
        else:
            bot.send_message(chat_id, "⚠️ El correo tardó demasiado o el Captcha fue incorrecto. Intenta de nuevo.")
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Ocurrió un error: {str(e)}")
    finally:
        # Cerramos todo para no gastar recursos del servidor
        s['browser'].close()
        s['pw'].stop()
        del sesiones[chat_id]

bot.polling()
        
