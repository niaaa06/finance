import os
import csv
import json
import re
import threading
from datetime import datetime
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

CSV_FILE = 'data_keuangan.csv'
TARGET_FILE = 'target.json'

def baca_csv():
    if not os.path.exists(CSV_FILE):
        return pd.DataFrame(columns=['Tanggal', 'Jenis', 'Kategori', 'Nominal', 'Keterangan'])
    df = pd.read_csv(CSV_FILE)
    df.columns = df.columns.str.strip().str.title()
    df['Nominal'] = pd.to_numeric(df['Nominal'], errors='coerce').fillna(0)
    return df

def simpan_baris_csv(tanggal, jenis, kategori, nominal, keterangan):
    file_exist = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
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
    bot.reply_to(message, "Halo! Bot pencatat keuangan Finance aktif. Kirim chat seperti 'Beli bakso 15rb' atau 'Nabung 100rb'.")

@bot.message_handler(func=lambda message: True)
def proses_chat_user(message):
    data = ekstrak_data_keuangan(message.text)
    if data:
        waktu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        simpan_baris_csv(waktu, data['jenis'], data['kategori'], data['nominal'], data['keterangan'])
        pesan = f"✅ *Berhasil Dicatat!*\nJenis: {data['jenis'].capitalize()}\nKategori: {data['kategori']}\nNominal: Rp {data['nominal']:,}"
        bot.send_message(message.chat.id, pesan, parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Gagal mengenali format uang.")

def jalankan_bot():
    bot.infinity_polling()

# ================= FLASK ROUTES =================
@app.route('/')
def index():
    df = baca_csv()
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

        df_sorted = df.reset_index().sort_values(by='Tanggal', ascending=False)
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
                           kategori_summary=kat_sum, warning=warning)

@app.route('/tambah_web', methods=['POST'])
def tambah_web():
    teks_input = request.form.get('teks_ai')
    if teks_input:
        data = ekstrak_data_keuangan(teks_input)
        if data:
            waktu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            simpan_baris_csv(waktu, data['jenis'], data['kategori'], data['nominal'], data['keterangan'])
    return redirect(url_for('index'))

@app.route('/set_target', methods=['POST'])
def update_target():
    simpan_target(int(request.form['target_nominal']))
    return redirect(url_for('index'))

@app.route('/hapus/<int:index_id>')
def hapus_transaksi(index_id):
    if os.path.exists(CSV_FILE):
        df = pd.read_csv(CSV_FILE)
        if 0 <= index_id < len(df):
            df = df.drop(index_id).reset_index(drop=True)
            df.to_csv(CSV_FILE, index=False)
    return redirect(request.referrer or url_for('histori'))

@app.route('/export')
def export_csv():
    if os.path.exists(CSV_FILE):
        return send_file(CSV_FILE, as_attachment=True, download_name='laporan_keuangan_finance.csv')
    return redirect(url_for('index'))

@app.route('/histori')
def histori():
    bulan_filter = request.args.get('bulan', datetime.now().strftime("%Y-%m"))
    df = baca_csv()
    if not df.empty:
        df['index_asli'] = df.index
        df_filtered = df[df['Tanggal'].str.startswith(bulan_filter)]
        df_filtered = df_filtered.sort_values(by='Tanggal', ascending=False)
        list_histori = df_filtered.to_dict('records')
    else:
        list_histori = []
    return render_template('histori.html', transaksi=list_histori, bulan=bulan_filter)


# ================= JALANKAN BOT DI BACKGROUND THREAD (GLOBAL) =================
# Supaya ikut terpanggil saat dimuat oleh Gunicorn/Railway
t = threading.Thread(target=jalankan_bot)
t.daemon = True
t.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
