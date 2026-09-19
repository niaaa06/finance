import os
import csv
import json
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
import telebot
from google import genai
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

TARGET_FILE = 'target.json'

# Fungsi untuk mendapatkan waktu lokal WIB (UTC+7)
def get_waktu_wib():
    return (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")

# Fungsi untuk mendapatkan nama file CSV khusus berdasarkan chat_id Telegram
def get_csv_file(chat_id):
    return f'data_keuangan_{chat_id}.csv'

def baca_csv(chat_id):
    csv_file = get_csv_file(chat_id)
    if not os.path.exists(csv_file):
        return pd.DataFrame(columns=['Tanggal', 'Jenis', 'Kategori', 'Nominal', 'Keterangan'])
    df = pd.read_csv(csv_file)
    df.columns = df.columns.str.strip().str.title()
    df['Nominal'] = pd.to_numeric(df['Nominal'], errors='coerce').fillna(0)
    return df

def simpan_baris_csv(chat_id, tanggal, jenis, kategori, nominal, keterangan):
    csv_file = get_csv_file(chat_id)
    file_exist = os.path.exists(csv_file)
    with open(csv_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exist:
            writer.writerow(['Tanggal', 'Jenis', 'Kategori', 'Nominal', 'Keterangan'])
        writer.writerow([tanggal, jenis, kategori, nominal, keterangan])

def baca_target():
    if os.path.exists(TARGET_FILE):
        with open(TARGET_FILE, 'r') as f:
            try:
                return json.load(f).get('target', 1000000)
            except:
                return 1000000
    return 1000000

def simpan_target(target):
    with open(TARGET_FILE, 'w') as f:
        json.dump({'target': target}, f)

# ================= TELEGRAM BOT LOGIC =================
def ekstrak_data_keuangan(teks):
    prompt = f"""
    Ekstrak informasi dari teks berikut jadi format JSON.
    ATURAN SANGAT KETAT:
    1. Balas HANYA dengan JSON murni, JANGAN ADA teks pembuka/penutup.
    2. Format wajib: {{"jenis": "pengeluaran", "kategori": "Makanan/Minuman", "nominal": 20000, "keterangan": "Beli kopi"}}
    (Catatan: Jika menabung/pemasukan, jenis diisi "pemasukan", jika jajan/belanja diisi "pengeluaran").
    Teks pengguna: "{teks}"
    """
    try:
        response = client.models.generate_content(model='gemini-3.6-flash', contents=prompt)
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return None
    except Exception as e:
        print("Error parsing AI:", e)
        return None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Halo! Bot pencatat keuangan pribadi aktif. \n\nPerintah:\n- Kirim teks pengeluaran/pemasukan (cth: 'Beli makan 25rb')\n- /id (Melihat Chat ID kamu)\n- /web (Mendapatkan link dashboard web kamu)")

@bot.message_handler(commands=['id'])
def send_id(message):
    bot.reply_to(message, f"Chat ID kamu adalah: `{message.chat.id}`", parse_mode="Markdown")

@bot.message_handler(commands=['web'])
def send_web_link(message):
    chat_id = message.chat.id
    web_url = f"https://finance-production-0fdb.up.railway.app/?user={chat_id}"
    bot.reply_to(message, f"🔗 Link dashboard web keuangan kamu:\n{web_url}")

@bot.message_handler(func=lambda message: True)
def proses_chat_user(message):
    chat_id = message.chat.id
    data = ekstrak_data_keuangan(message.text)
    if data:
        waktu = get_waktu_wib()
        simpan_baris_csv(chat_id, waktu, data['jenis'], data['kategori'], data['nominal'], data['keterangan'])
        pesan = f"✅ *Berhasil Dicatat!*\nJenis: {data['jenis'].capitalize()}\nKategori: {data['kategori']}\nNominal: Rp {data['nominal']:,}"
        bot.send_message(chat_id, pesan, parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Gagal mengenali format uang atau kuota AI habis.")

def jalankan_bot():
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"Polling error: {e}")

# ================= FLASK ROUTES =================
@app.route('/')
def index():
    chat_id = request.args.get('user', 'default')
    df = baca_csv(chat_id)
    target = baca_target()

    if df.empty:
        saldo, pemasukan, pengeluaran, progress = 0, 0, 0, 0
        transaksi_terakhir = []
        kat_sum = []
        warning = False
    else:
        pemasukan = df[df['Jenis'].str.lower() == 'pemasukan']['Nominal'].sum()
        pengeluaran = df[df['Jenis'].str.lower() == 'pengeluaran']['Nominal'].sum()
        saldo = pemasukan - pengeluaran

        df['index_asli'] = df.index
        df_sorted = df.sort_values(by='Tanggal', ascending=False)
        transaksi_terakhir = df_sorted.head(5).to_dict('records')

        progress = int((saldo / target) * 100) if target > 0 else 0
        progress = max(0, min(progress, 100))

        warning = (pemasukan > 0) and (pengeluaran >= 0.8 * pemasukan)

        df_keluar = df[df['Jenis'].str.lower() == 'pengeluaran']
        if not df_keluar.empty:
            kat_sum = df_keluar.groupby('Kategori')['Nominal'].sum().reset_index()
            kat_sum = kat_sum.sort_values(by='Nominal', ascending=False).to_dict('records')
        else:
            kat_sum = []

    return render_template('index.html', saldo=saldo, total_in=pemasukan, total_out=pengeluaran,
                           transaksi=transaksi_terakhir, target=target, progress=progress,
                           kategori_summary=kat_sum, warning=warning, user=chat_id)

@app.route('/tambah_web', methods=['POST'])
def tambah_web():
    chat_id = request.args.get('user', 'default')
    teks_input = request.form.get('teks_ai')
    if teks_input:
        data = ekstrak_data_keuangan(teks_input)
        if data:
            waktu = get_waktu_wib()
            simpan_baris_csv(chat_id, waktu, data['jenis'], data['kategori'], data['nominal'], data['keterangan'])
    return redirect(url_for('index', user=chat_id))

# Rute Tambah Manual (Cadangan saat token AI habis)
@app.route('/tambah_manual', methods=['POST'])
def tambah_manual():
    chat_id = request.args.get('user', 'default')
    jenis = request.form.get('jenis')
    kategori = request.form.get('kategori', 'Lainnya')
    try:
        nominal = float(request.form.get('nominal', 0))
    except ValueError:
        nominal = 0
    keterangan = request.form.get('keterangan', '-')
    
    if nominal > 0:
        waktu = get_waktu_wib()
        simpan_baris_csv(chat_id, waktu, jenis, kategori, nominal, keterangan)
        
    return redirect(url_for('index', user=chat_id))

@app.route('/set_target', methods=['POST'])
def update_target():
    chat_id = request.args.get('user', 'default')
    simpan_target(int(request.form['target_nominal']))
    return redirect(url_for('index', user=chat_id))

@app.route('/hapus/<int:index_id>')
def hapus_transaksi(index_id):
    chat_id = request.args.get('user', 'default')
    csv_file = get_csv_file(chat_id)
    if os.path.exists(csv_file):
        df = pd.read_csv(csv_file)
        if 0 <= index_id < len(df):
            df = df.drop(index_id).reset_index(drop=True)
            df.to_csv(csv_file, index=False)
            
    asal = request.args.get('from', '')
    if asal == 'index':
        return redirect(url_for('index', user=chat_id))
    return redirect(url_for('histori', user=chat_id))

@app.route('/export')
def export_csv():
    chat_id = request.args.get('user', 'default')
    csv_file = get_csv_file(chat_id)
    if os.path.exists(csv_file):
        return send_file(csv_file, as_attachment=True, download_name=f'laporan_keuangan_{chat_id}.csv')
    return redirect(url_for('index', user=chat_id))

@app.route('/histori')
def histori():
    chat_id = request.args.get('user', 'default')
    bulan_filter = request.args.get('bulan', (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m"))
    df = baca_csv(chat_id)
    if not df.empty:
        df['index_asli'] = df.index
        df_filtered = df[df['Tanggal'].str.startswith(bulan_filter)]
        df_filtered = df_filtered.sort_values(by='Tanggal', ascending=False)
        list_histori = df_filtered.to_dict('records')
    else:
        list_histori = []
    return render_template('histori.html', transaksi=list_histori, bulan=bulan_filter, user=chat_id)

# Jalankan bot di background thread secara aman
t = threading.Thread(target=jalankan_bot)
t.daemon = True
t.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
