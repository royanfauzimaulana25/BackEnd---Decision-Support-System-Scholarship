import os
import asyncio
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from supabase import create_client, Client

# --- KONFIGURASI SUPABASE ---
load_dotenv()

# Ambil URL dan Kunci dari environment
# Connect Supabase via API
url : str = os.environ.get('SUPABASE_API_URL_DSS')
key : str = os.environ.get('SUPABASE_API_KEY_DSS')

DATABASE_URL = os.getenv("SUPABASE_DB_URL_DSS")

supabase: Client = create_client(url, key)

# Tentukan ID periode beasiswa yang akan dihitung
ID_PERIODE_AKTIF = 1

async def main():
    """Fungsi utama untuk menjalankan seluruh proses perhitungan SAW dengan Supabase."""
    print("--- Memulai Proses Perhitungan SAW (Supabase) ---")

    # 1. Mengambil data dari Supabase secara bersamaan (concurrently)
    print(f"\n1. Mengambil data untuk periode ID: {ID_PERIODE_AKTIF}...")

    kriteria_response = supabase.table("kriteria_saw").select("*").order("id_kriteria").execute()

    # Mengambil data pendaftar dan melakukan join ke tabel siswa
    pendaftar_response = supabase.table("pendaftaran") \
        .select(
        "id_pendaftaran, id_siswa, id_periode, status_validasi, penghasilan_orangtua, jumlah_tanggungan,luas_rumah, rerata_nilai, peringkat_kelas, siswa(nama_siswa)") \
        .eq("id_periode", ID_PERIODE_AKTIF) \
        .eq("status_validasi", "valid") \
        .execute()

    # Menunggu semua query selesai
    # kriteria_response, pendaftar_response = asyncio.gather(kriteria_task, pendaftar_task)

    kriteria = pd.DataFrame(kriteria_response.data)
    pendaftar = pd.DataFrame(pendaftar_response.data)

    if pendaftar.empty:
        print("\nTidak ada data pendaftar yang valid untuk dihitung pada periode ini.")

    # Menggabungkan nama_siswa dari hasil join
    pendaftar['nama_siswa'] = pendaftar['siswa'].apply(lambda x: x['nama_siswa'] if isinstance(x, dict) else None)
    pendaftar = pendaftar.drop(columns=['siswa'])  # Hapus kolom join yang sudah tidak perlu

    print(f"   - Ditemukan {len(kriteria)} kriteria.")
    print(f"   - Ditemukan {len(pendaftar)} pendaftar yang valid.")

    # 2. Membuat Matriks Keputusan (X) - Logika ini tidak berubah
    print("\n2. Membuat Matriks Keputusan (X)...")
    matriks_x = pd.DataFrame(pendaftar['id_pendaftaran'])

    kolom_mapping = {
        'C1': 'penghasilan_orangtua',
        'C2': 'peringkat_kelas',
        'C3': 'jumlah_tanggungan',
        'C4': 'luas_rumah',
        'C5': 'rerata_nilai'
    }

    for _, krit in kriteria.iterrows():
        kode = krit['kode_kriteria']
        kolom = kolom_mapping[kode]

        # Logika untuk setiap kriteria menggunakan np.select yang mudah dibaca
        if kode == 'C1':  # Penghasilan Orang Tua
            conditions = [
                pendaftar[kolom] <= 500000,
                pendaftar[kolom] <= 1000000,
                pendaftar[kolom] <= 1500000,
                pendaftar[kolom] <= 2000000,
            ]
            choices = [1.00, 0.75, 0.50, 0.25]
            matriks_x[kode] = np.select(conditions, choices, default=0.00)

        elif kode == 'C2':  # Rangking
            conditions = [
                pendaftar[kolom] <= 5,
                pendaftar[kolom] <= 10,
                pendaftar[kolom] <= 15,
                pendaftar[kolom] <= 20,
            ]
            choices = [1.00, 0.75, 0.50, 0.25]
            matriks_x[kode] = np.select(conditions, choices, default=0.00)

        elif kode == 'C3':  # Jumlah Tanggungan
            conditions = [
                pendaftar[kolom] >= 5,
                pendaftar[kolom] == 4,
                pendaftar[kolom] == 3,
                pendaftar[kolom] == 2,
            ]
            choices = [1.00, 0.75, 0.50, 0.25]
            matriks_x[kode] = np.select(conditions, choices, default=0.00)

        elif kode == 'C4':  # Luas Rumah
            conditions = [
                pendaftar[kolom] < 36,
                pendaftar[kolom] <= 54,
                pendaftar[kolom] <= 70,
                pendaftar[kolom] <= 100,
            ]
            choices = [1.00, 0.75, 0.50, 0.25]
            matriks_x[kode] = np.select(conditions, choices, default=0.00)

        elif kode == 'C5':  # Nilai
            conditions = [
                pendaftar[kolom] > 90,
                pendaftar[kolom] > 80,
                pendaftar[kolom] > 70,
                pendaftar[kolom] > 40,
            ]
            choices = [1.00, 0.75, 0.50, 0.25]
            matriks_x[kode] = np.select(conditions, choices, default=0.00)

    print("   Matriks Keputusan (X):")
    print(matriks_x)

    # 3. Normalisasi Matriks (R) - Logika ini tidak berubah
    print("\n3. Melakukan Normalisasi Matriks (R)...")
    matriks_r = matriks_x.copy()
    for _, krit in kriteria.iterrows():
        kode = krit['kode_kriteria']
        jenis = krit['jenis']

        if jenis == 'benefit':
            max_val = matriks_x[kode].max()
            if max_val > 0:
                matriks_r[kode] = matriks_x[kode] / max_val
        elif jenis == 'cost':
            min_val = matriks_x[kode].min()
            matriks_r[kode] = matriks_x[kode].apply(lambda x: min_val / x if x > 0 else (1 if min_val == 0 else 0))

    print("   Matriks Ternormalisasi (R):")
    print(matriks_r.round(2))

    # 4. Perangkingan (Menghitung Nilai Preferensi V) - Logika ini tidak berubah
    print("\n4. Menghitung Nilai Akhir dan Perangkingan...")

    bobot_w = kriteria.set_index('kode_kriteria')['normalize_bobot']
    nilai_akhir = (matriks_r * bobot_w).sum(axis=1)

    hasil = pd.DataFrame({
        'id_pendaftaran': pendaftar['id_pendaftaran'],
        'nama_siswa': pendaftar['nama_siswa'],
        'nilai_akhir': nilai_akhir
    }).sort_values(by='nilai_akhir', ascending=False).reset_index(drop=True)

    # print(hasil)
    # matriks_r

    hasil['peringkat'] = hasil.index + 1
    hasil['status_rekomendasi'] = hasil['peringkat'].apply(
        lambda x: 'direkomendasikan' if x <= 5 else 'tidak direkomendasikan'
    )

    hasil_untuk_db = hasil[['id_pendaftaran', 'nilai_akhir', 'peringkat', 'status_rekomendasi']].copy()
    hasil_untuk_db['id_periode'] = ID_PERIODE_AKTIF
    hasil_untuk_db['is_publish'] = False

    print("Hasil Akhir Perangkingan:")
    hasil_for_beasiswa = hasil.to_json(orient='records')
    hasil_for_database = hasil_untuk_db.to_json(orient='records')
    print(hasil_for_database)

    # 5. Menyimpan hasil ke Supabase
    # save_results_to_db(hasil_untuk_db)

    print("\n--- Proses Selesai ---")
    return hasil_for_beasiswa,  hasil_for_database


if __name__ == '__main__':
    asyncio.run(main())
