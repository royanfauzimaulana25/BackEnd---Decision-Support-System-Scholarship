from fastapi import FastAPI, UploadFile, File, HTTPException, Form, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

import asyncpg
import os
from dotenv import load_dotenv
from datetime import date
from typing import Optional, Dict, Annotated, List, Literal
import hypercorn
from supabase import create_client, Client
from calculate_saw import main
import json
from datetime import datetime

load_dotenv()  # loads from .env file

# Connect Supabase via API
url : str = os.environ.get('SUPABASE_API_URL_DSS')
key : str = os.environ.get('SUPABASE_API_KEY_DSS')

DATABASE_URL = os.getenv("SUPABASE_DB_URL_DSS")

supabase: Client = create_client(url, key)
app = FastAPI(
    title="Scholarship Decision Support System API",
    version="1.0.0",
    description="Dokumentasi API untuk sistem penunjang keputusan beasiswa"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_db():
    return await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

# ===========================================================================
# Models
# ===========================================================================
# --- MODEL PYDANTIC ---
class DeleteResponse(BaseModel):
    message: str
    id_pendaftaran_dihapus: int

class IsPublishResponse(BaseModel):
    is_publish: bool

class PublishStatusUpdate(BaseModel):
    is_publish: bool

class SiswaCheckRequest(BaseModel):
    nisn: str
    nis: str
    nik: str
    tanggal_lahir: date

# Model untuk data yang dikembalikan oleh server jika sukses
class SiswaCheckResponse(BaseModel):
    id_siswa: int
    sudah_mendaftar: bool

class PendaftaranStatusResponse(BaseModel):
    sudah_mendaftar: bool

class SiswaDetailResponse(BaseModel):
    nis: Optional[str] = None
    nisn: Optional[str] = None
    nik: Optional[str] = None
    tanggal_lahir: Optional[date] = None
    nama_siswa: str
    kelas: Optional[str] = "Belum ada kelas"

class TracerData(BaseModel):
    id_alumni: int
    alamat_email: str
    no_telepon: str
    status: str
    perguruan_tinggi: str
    program_studi: str
    sumber_biaya: str
    tahun_masuk: int
    jawaban_kuesioner: dict

class SiswaCreate(BaseModel):
    id_kelas: int
    nis: str
    nisn: str
    nik: str
    nama_siswa: str
    tanggal_lahir: date
    alamat_email: Optional[str] = None
    no_telepon: Optional[str] = None

class LoginRequest(BaseModel):
    email: str = Form(...)
    password: str = Form(...)

class PersonalData(BaseModel):
    alamat_email: str
    no_telepon: str

class DetailKeluarga(BaseModel):
    jumlah_tanggungan: int
    luas_rumah: int
    penghasilan_orangtua: int
    peringkat_kelas: int
    rerata_nilai: int

class SubmissionPayload(BaseModel):
    id_siswa: str
    personal_data: PersonalData
    detailKeluarga: DetailKeluarga = None

# Model untuk output statistik
class StatistikPendaftaranResponse(BaseModel):
    jumlah_pendaftar: int
    rerata_nilai: float
    rerata_peringkat: float

class PersonalData(BaseModel):
    id_siswa: int
    nis: Optional[str] = None
    nisn: Optional[str] = None
    nik: Optional[str] = None
    tanggal_lahir: Optional[date] = None
    nama_siswa: str
    kelas: Optional[str] = "Belum ada kelas"
    alamat_email: Optional[str] = None
    no_telepon: Optional[str] = None

class PendaftaranData(BaseModel):
    id_pendaftaran: int
    status_validasi: str
    penghasilan_orangtua : int
    jumlah_tanggungan : int
    luas_rumah : int
    peringkat_kelas : int
    rerata_nilai : int
    file_keterangan_penghasilan : str
    file_kartu_keluarga : str
    file_pbb : str
    file_rapor : str
    # Tambahkan field lain dari tabel 'pendaftaran' jika perlu
    # contoh: tanggal_daftar, dll.

class SiswaDataResponse(BaseModel):
    personal_data: PersonalData
    pendaftaran_data: PendaftaranData = None

class StatusUpdateRequest(BaseModel):
    # Gunakan Literal untuk membatasi nilai yang diterima
    status_validasi: Literal['valid', 'tidak valid']

class RankDetailResponse(BaseModel):
    nama_siswa: str
    kelas: Optional[str] = "N/A"
    penghasilan_orangtua: Optional[int] = 0
    jumlah_tanggungan: Optional[int] = 0
    luas_rumah: Optional[int] = 0
    rerata_nilai: Optional[float] = 0.0
    peringkat_kelas: Optional[int] = 0
    skor: float
    status_rekomendasi: str

class SuccessResponse(BaseModel):
    message: str
    records_processed: int

# ===========================================================================
# Auth
# ===========================================================================

@app.post("/login", tags=["OAuth"])
async def login(data: LoginRequest):
    """Endpoint login untuk mengakses Platform.

    - **email**: String email
    - **password**: String password
    """

    conn = await get_db()
    result = await conn.fetchrow('SELECT nama FROM "admin" WHERE username=$1 AND password=$2', data.email, data.password)

    await conn.close()
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"message": "Login successful", "data": dict(result)}

# ===========================================================================
# Pendaftaran Beasiswa
# ===========================================================================
@app.delete(
    "/pendaftaran/{id_pendaftaran}",
    response_model=DeleteResponse,
    tags=["Pendaftaran Beasiswa"],
    summary="Hapus Data Pendaftaran",
    description="Menghapus data pendaftaran dan semua file terkait dari Supabase."
)
async def delete_pendaftaran(id_pendaftaran: int):
    """
    Endpoint untuk menghapus sebuah record pendaftaran berdasarkan ID-nya.
    """
    try:
        delete_response = supabase.table("pendaftaran").delete().eq("id_pendaftaran", id_pendaftaran).execute()

        if not delete_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Gagal menghapus record dari database (mungkin sudah terhapus)."
            )

        return DeleteResponse(
            message="Data pendaftaran dan file terkait berhasil dihapus.",
            id_pendaftaran_dihapus=id_pendaftaran
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan pada server: {str(e)}"
        )

@app.post(
    "/beasiswa/rank/save",
    response_model=SuccessResponse,
    tags=["Perhitungan Beasiswa"],
    summary="Simpan Hasil Peringkat Beasiswa",
    description="Menjalankan perhitungan SAW, lalu menyimpan hasilnya ke database."
)
async def save_rank_beasiswa():
    """
    Menjalankan perhitungan SAW, lalu menghapus hasil lama dan menyimpan
    satu set hasil perhitungan yang baru untuk periode yang relevan.
    """
    try:
        # 1. Jalankan fungsi utama untuk mendapatkan hasil perhitungan
        # Asumsi: main() mengembalikan JSON string dari hasil DataFrame
        rank_results_str, hasil_for_database = await main()
        # Parse string JSON menjadi list of dictionaries
        data_to_insert = json.loads(hasil_for_database)
        print(data_to_insert)
        # 2. Pastikan ada data untuk diproses
        if not data_to_insert:
            return SuccessResponse(message="Tidak ada data untuk disimpan.", records_processed=0)

        # 3. Dapatkan id_periode secara dinamis dari data pertama
        # Ini lebih aman daripada menggunakan variabel global
        id_periode = 1
        if not id_periode:
             raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Data hasil perhitungan tidak mengandung 'id_periode'."
            )

    except Exception as e:
         raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal saat menjalankan atau mem-parsing hasil perhitungan: {str(e)}"
        )

    try:
        # 4. Hapus hasil lama untuk periode ini (tambahkan await)
        print(f"Menghapus hasil lama untuk periode ID: {id_periode}...")
        supabase.table("hasil_saw").delete().eq("id_periode", id_periode).execute()

        # 5. Masukkan hasil baru menggunakan upsert (tambahkan await)
        # 'data_to_insert' sudah dalam format yang benar (list of dicts)
        print(f"Memasukkan {len(data_to_insert)} hasil baru...")
        print(data_to_insert)
        response = supabase.table("hasil_saw").upsert(data_to_insert).execute()

        if not response.data:
             raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Gagal menyimpan data baru ke Supabase."
            )

        return SuccessResponse(
            message=f"Berhasil menyimpan hasil peringkat untuk periode {id_periode}.",
            records_processed=len(response.data)
        )

    except Exception as e:
        # Gunakan HTTPException untuk mengembalikan error yang proper
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saat menyimpan hasil ke Supabase: {str(e)}"
        )

@app.get(
    "/beasiswa/rank",
    response_model=List[RankDetailResponse],
    tags=["Perhitungan Beasiswa"],
    summary="Dapatkan Hasil Peringkat Beasiswa Lengkap",
    description="Menjalankan perhitungan SAW dan mengembalikan hasil peringkat beserta data detail pendaftar."
)
async def get_rank_beasiswa():
    """
    Endpoint ini melakukan dua langkah utama:
    1. Menjalankan fungsi perhitungan SAW untuk mendapatkan peringkat dasar.
    2. Mengambil data detail pendaftar dari database berdasarkan hasil peringkat.
    3. Menggabungkan kedua data tersebut untuk respons yang lengkap.
    """
    try:
        # 1. Jalankan fungsi perhitungan SAW Anda
        # Asumsi: main() sekarang mengembalikan list of dictionaries, bukan JSON string
        # Jika main() masih mengembalikan JSON string, kita parse dulu
        rank_results_str,  hasil_for_database_str = await main()  # Ganti 'main' dengan nama fungsi Anda jika berbeda
        rank_results = json.loads(rank_results_str)

        if not rank_results:
            return []

        # 2. Ambil semua 'id_pendaftaran' dari hasil peringkat
        pendaftaran_ids = [item['id_pendaftaran'] for item in rank_results]

        # 3. Ambil data detail dari Supabase untuk semua ID yang relevan
        response = supabase.table("pendaftaran") \
            .select("*, siswa(*, kelas(nama_kelas))") \
            .in_("id_pendaftaran", pendaftaran_ids) \
            .execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Data detail pendaftar tidak ditemukan.")

        # 4. Buat 'lookup map' untuk penggabungan data yang efisien
        detail_map = {item['id_pendaftaran']: item for item in response.data}

        # 5. Gabungkan hasil peringkat dengan data detail
        final_response = []
        for rank_item in rank_results:
            pendaftaran_id = rank_item['id_pendaftaran']
            detail_data = detail_map.get(pendaftaran_id)

            if not detail_data:
                continue  # Lewati jika data detail tidak ditemukan

            kelas_data = detail_data.get('siswa', {}).get('kelas')

            # Buat objek respons yang lengkap
            full_data = RankDetailResponse(
                nama_siswa=rank_item.get('nama_siswa'),
                kelas=kelas_data.get('nama_kelas') if kelas_data else "N/A",
                penghasilan_orangtua=detail_data.get('penghasilan_orangtua'),
                jumlah_tanggungan=detail_data.get('jumlah_tanggungan'),
                luas_rumah=detail_data.get('luas_rumah'),
                rerata_nilai=detail_data.get('rerata_nilai'),
                peringkat_kelas=detail_data.get('peringkat_kelas'),
                skor=rank_item.get('nilai_akhir'),
                status_rekomendasi=rank_item.get('status_rekomendasi')
            )
            final_response.append(full_data)

        return final_response

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan: {str(e)}"
        )

@app.post(
    "/siswa/check",
    response_model=SiswaCheckResponse,
    tags=["Pendaftaran Beasiswa"],
    summary="Verifikasi Data Siswa",
    description="Mengecek apakah siswa ada di database berdasarkan NISN, NIS, NIK, dan Tanggal Lahir."
)
async def check_siswa(request_data: SiswaCheckRequest):
    try:
        response = supabase.table("siswa") \
            .select("id_siswa") \
            .eq("nisn", request_data.nisn) \
            .eq("nis", request_data.nis) \
            .eq("nik", request_data.nik) \
            .eq("tanggal_lahir", str(request_data.tanggal_lahir)) \
            .maybe_single() \
            .execute()

        siswa_data = response.data

        # Jika tidak ada data yang ditemukan
        if not siswa_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Siswa tidak ditemukan dengan kombinasi data yang diberikan."
            )

        # Sekarang kita bisa menggunakan .get() dengan aman pada dictionary siswa_data
        id_siswa = siswa_data.get("id_siswa")

        # Cek apakah list 'pendaftaran' berisi data. Jika ya, berarti sudah mendaftar.
        # Logika ini sekarang berfungsi karena kita meminta data pendaftaran di .select()
        sudah_mendaftar = bool(siswa_data.get("pendaftaran"))

        return SiswaCheckResponse(
            id_siswa=id_siswa,
            sudah_mendaftar=sudah_mendaftar
        )

    except Exception as e:
        # Menangani kemungkinan error lain dari Supabase atau proses
        print(e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/beasiswa/daftar/submit", tags=["Pendaftaran Beasiswa"])
async def submit_pendaftaran(
        # Menerima string JSON dari field form bernama 'payload'
        payload_str: str = Form(..., alias="payload"),

        # Menerima file-file (opsional)
        file_keterangan_penghasilan: Optional[UploadFile] = File(None),
        file_kartu_keluarga: Optional[UploadFile] = File(None),
        file_pbb: Optional[UploadFile] = File(None),
        file_rapor: Optional[UploadFile] = File(None)
):
    """
    Menerima data pendaftaran, mengunggah file ke Supabase Storage,
    dan memasukkan data ke tabel Supabase.
    """
    # 1. Validasi data JSON dari payload
    try:
        payload = SubmissionPayload.parse_raw(payload_str)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Struktur data JSON pada 'payload' tidak valid.", "errors": e.errors()}
        )

    # 2. Upload file ke Supabase Storage dan kumpulkan URL-nya
    file_urls = {}
    files_to_process = {
        "file_keterangan_penghasilan": file_keterangan_penghasilan,
        "file_kartu_keluarga": file_kartu_keluarga,
        "file_pbb": file_pbb,
        "file_rapor": file_rapor,
    }

    for field_name, upload_file in files_to_process.items():
        if upload_file:
            try:
                # Baca konten file
                contents = upload_file.file.read()
                # Tentukan path di dalam bucket
                file_path = f"{payload.id_siswa}-{field_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

                # Upload file
                supabase.storage.from_('berkas-pendukung').upload(
                    path=file_path,
                    file=contents,
                    file_options={"content-type": upload_file.content_type}
                )

                # Dapatkan URL publik dari file yang di-upload
                response = supabase.storage.from_('berkas-pendukung').get_public_url(file_path)
                file_urls[field_name] = response

            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Gagal mengunggah file '{upload_file.filename}': {str(e)}"
                )

    # 3. Siapkan data untuk dimasukkan ke tabel 'pendaftaran'
    print(file_urls)
    pendaftaran_data = {
        "id_siswa": payload.id_siswa,
        "id_periode": 1,
        **payload.detailKeluarga.dict(),  # Gabungkan semua data dari detail_keluarga
        # Tambahkan path file
        "file_keterangan_penghasilan": file_urls.get("file_keterangan_penghasilan"),
        "file_kartu_keluarga": file_urls["file_kartu_keluarga"],
        "file_pbb": file_urls.get("file_pbb"),
        "file_rapor": file_urls.get("file_rapor"),
        "status_validasi": "belum divalidasi"  # Set status awal
    }

    try:
        # 4. Update data siswa (email & no_telepon) di tabel 'siswa'
        supabase.table("siswa").update(payload.personal_data.dict()).eq("id_siswa", payload.id_siswa).execute()

        # 5. Masukkan data pendaftaran ke tabel 'pendaftaran'
        insert_response =  supabase.table("pendaftaran").insert(pendaftaran_data).execute()

        if not insert_response.data:
            raise HTTPException(status_code=500, detail="Gagal menyimpan data pendaftaran ke database.")

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan pada database: {str(e)}"
        )

    return {
        "message": "Pendaftaran berhasil diterima!",
        "data_tersimpan": insert_response.data[0]
    }

@app.patch(
    "/pendaftaran/status/{id_pendaftaran}",
    tags=["Pendaftaran Beasiswa"],
    summary="Update Status Validasi Pendaftaran",
    description="Mengubah status validasi sebuah pendaftaran menjadi 'valid' atau 'tidak valid'."
)
async def update_pendaftaran_status(id_pendaftaran: int, status_update: StatusUpdateRequest):
    """
    Endpoint untuk mengupdate status_validasi dari sebuah pendaftaran.
    Hanya menerima 'valid' atau 'tidak valid' sebagai nilai baru.
    """
    try:
        # Update data di tabel 'pendaftaran' berdasarkan id_pendaftaran
        response = supabase.table("pendaftaran") \
            .update({"status_validasi": status_update.status_validasi}) \
            .eq("id_pendaftaran", id_pendaftaran) \
            .execute()

        # Jika tidak ada baris yang diupdate, berarti ID tidak ditemukan
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Pendaftaran dengan ID {id_pendaftaran} tidak ditemukan."
            )

        return {"message": "Status berhasil diperbarui", "data": response.data[0]}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan pada server: {str(e)}"
        )

@app.get(
    "/pendaftaran/status/{id_siswa}",
    response_model=PendaftaranStatusResponse,
    tags=["Pendaftaran Beasiswa"],
    summary="Cek Status Pendaftaran Siswa",
    description="Mengecek apakah siswa dengan ID tertentu sudah pernah mendaftar beasiswa."
)
async def check_pendaftaran_status(id_siswa: int):
    """
    Endpoint ini akan memeriksa tabel `pendaftaran` untuk record yang cocok
    dengan `id_siswa` yang diberikan.

    - Jika ditemukan, akan mengembalikan `{"sudah_mendaftar": true}` beserta ID pendaftarannya.
    - Jika tidak ditemukan, akan mengembalikan `{"sudah_mendaftar": false}`.
    """
    try:
        # Query ke Supabase untuk mencari data.
        # Kita hanya butuh 'id_pendaftaran' dan membatasi hanya 1 hasil untuk efisiensi.
        response = supabase.table("pendaftaran") \
            .select("id_pendaftaran") \
            .eq("id_siswa", id_siswa) \
            .limit(1) \
            .execute()

        # Jika query mengembalikan data (list tidak kosong)
        if response.data:
            return PendaftaranStatusResponse(
                sudah_mendaftar=True,
                id_pendaftaran=response.data[0]['id_pendaftaran']
            )

        # Jika tidak ada data yang ditemukan
        return PendaftaranStatusResponse(sudah_mendaftar=False)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan saat memeriksa data: {str(e)}")


@app.get(
    "/siswa/pendaftar",  # Nama endpoint diubah agar lebih deskriptif
    response_model=List[SiswaDataResponse],
    tags=["Siswa"],
    summary="Dapatkan Semua Siswa yang Sudah Mendaftar",
    description="Mengambil data siswa yang memiliki data pendaftaran beasiswa."
)
async def get_all_pendaftar():
    """
    Endpoint ini melakukan INNER JOIN untuk mendapatkan daftar siswa yang sudah mendaftar.
    """
    try:
        # --- PERUBAHAN UTAMA: Menggunakan INNER JOIN ---
        # Ganti !left(*) menjadi !inner(*) untuk hanya mengambil siswa yang punya data pendaftaran.
        response = supabase.table("siswa") \
            .select("*, kelas(nama_kelas), pendaftaran!inner(*)") \
            .order("id_siswa", desc=True) \
            .execute()

        if not response.data:
            return []

        # --- Transformasi Data yang Disederhanakan ---
        results = []
        for record in response.data:
            kelas_data = record.get("kelas")
            personal_data_obj = PersonalData(
                id_siswa=record.get("id_siswa"),
                nis=record.get("nis"),
                nisn=record.get("nisn"),
                nik=record.get("nik"),
                tanggal_lahir=record.get("tanggal_lahir"),
                nama_siswa=record.get("nama_siswa"),
                kelas=kelas_data.get("nama_kelas") if kelas_data else "Belum ada kelas",
                alamat_email=record.get("alamat_email"),
                no_telepon=record.get("no_telepon")
            )

            # Karena menggunakan INNER JOIN, 'pendaftaran' dijamin ada.
            pendaftaran_raw = record["pendaftaran"][0]
            pendaftaran_data_obj = PendaftaranData(**pendaftaran_raw)

            results.append(SiswaDataResponse(
                personal_data=personal_data_obj,
                pendaftaran_data=pendaftaran_data_obj
            ))

        return results

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan saat mengambil data siswa: {str(e)}")


# ===========================================================================
# Siswa
# ===========================================================================
@app.get(
    "/siswa/detail/{id_siswa}",
    response_model=SiswaDetailResponse,
    tags=["Siswa"],
    summary="Dapatkan Detail Siswa",
    description="Mengambil data spesifik seorang siswa berdasarkan ID-nya."
)
async def get_siswa_by_id(id_siswa: int):
    """
    Endpoint ini mengambil detail siswa, termasuk nama kelasnya, berdasarkan `id_siswa`.
    """
    try:
        # Query ke Supabase untuk mengambil data dari tabel 'siswa'
        # dan melakukan 'join' ke tabel 'kelas'
        response = supabase.table("siswa") \
            .select("nis, nisn, nik, tanggal_lahir, nama_siswa, kelas(nama_kelas)") \
            .eq("id_siswa", id_siswa) \
            .maybe_single() \
            .execute()

        siswa_data = response.data

        # Jika siswa dengan ID tersebut tidak ditemukan
        if not siswa_data:
            raise HTTPException(
                status_code=404,
                detail=f"Siswa dengan ID {id_siswa} tidak ditemukan."
            )

        # --- Transformasi Data ---
        # Respon dari Supabase untuk join akan berbentuk nested dictionary.
        # Kita perlu meratakannya agar sesuai dengan model Pydantic.
        kelas_data = siswa_data.get("kelas")

        result = {
            "nis": siswa_data.get("nis"),
            "nisn": siswa_data.get("nisn"),
            "nik": siswa_data.get("nik"),
            "tanggal_lahir": siswa_data.get("tanggal_lahir"),
            "nama_siswa": siswa_data.get("nama_siswa"),
            "kelas": kelas_data.get("nama_kelas") if kelas_data else "Belum ada kelas"
        }
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/siswa/all",
    response_model=List[SiswaDataResponse],
    tags=["Siswa"],
    summary="Dapatkan Semua Data Siswa dan Pendaftarannya",
    description="Mengambil seluruh data siswa, termasuk data pendaftaran jika ada."
)
async def get_all_siswa():
    """
    Endpoint ini melakukan query ke Supabase untuk mendapatkan daftar semua siswa.
    - Menggunakan LEFT JOIN untuk menyertakan data pendaftaran.
    - Jika siswa belum mendaftar, `pendaftaran_data` akan bernilai `null`.
    """
    try:
        # Query Supabase dengan LEFT JOIN ke tabel pendaftaran
        # Syntax !left(*) adalah cara PostgREST untuk melakukan LEFT JOIN
        response = supabase.table("siswa") \
            .select("*, kelas(nama_kelas), pendaftaran!left(*)") \
            .order("id_siswa", desc=True) \
            .execute()

        if not response.data:
            return []

        # --- Transformasi Data ---
        # Respon Supabase perlu diubah strukturnya agar sesuai dengan model output
        results = []
        for record in response.data:
            # Siapkan data personal
            kelas_data = record.get("kelas")
            personal_data_obj = PersonalData(
                id_siswa=record.get("id_siswa"),
                nis=record.get("nis"),
                nisn=record.get("nisn"),
                nik=record.get("nik"),
                tanggal_lahir=record.get("tanggal_lahir"),
                nama_siswa=record.get("nama_siswa"),
                kelas=kelas_data.get("nama_kelas") if kelas_data else "Belum ada kelas",
                alamat_email=record.get("alamat_email"),
                no_telepon=record.get("no_telepon")
            )

            # Siapkan data pendaftaran (jika ada)
            pendaftaran_data_list = record.get("pendaftaran", [])
            pendaftaran_data_obj = None
            if pendaftaran_data_list:
                # Ambil data pendaftaran pertama jika ada (biasanya hanya satu per siswa)
                pendaftaran_raw = pendaftaran_data_list[0]
                pendaftaran_data_obj = PendaftaranData(
                    id_pendaftaran=pendaftaran_raw.get("id_pendaftaran"),
                    status_validasi=pendaftaran_raw.get("status_validasi"),
                    penghasilan_orangtua=pendaftaran_raw.get("penghasilan_orangtua"),
                    jumlah_tanggungan=pendaftaran_raw.get("jumlah_tanggungan"),
                    luas_rumah=pendaftaran_raw.get("luas_rumah"),
                    peringkat_kelas=pendaftaran_raw.get("peringkat_kelas"),
                    rerata_nilai=pendaftaran_raw.get("rerata_nilai"),
                    file_keterangan_penghasilan=pendaftaran_raw.get("file_keterangan_penghasilan"),
                    file_kartu_keluarga=pendaftaran_raw.get("file_kartu_keluarga"),
                    file_pbb=pendaftaran_raw.get("file_pbb"),
                    file_rapor=pendaftaran_raw.get("file_rapor")
                    # Map field lain jika perlu
                )

            # Gabungkan menjadi satu objek dan tambahkan ke list hasil
            results.append(SiswaDataResponse(
                personal_data=personal_data_obj,
                pendaftaran_data=pendaftaran_data_obj
            ))

        return results

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan saat mengambil data siswa: {str(e)}")


@app.post(
    "/siswa",
    status_code=status.HTTP_201_CREATED,
    tags=["Siswa"],
    summary="Tambah Siswa Baru",
    description="Menambahkan data siswa baru ke dalam database."
)


async def create_siswa(siswa_data: SiswaCreate):
    """
    Endpoint untuk membuat record siswa baru.

    - Menerima data siswa dalam format JSON.
    - Mengembalikan data siswa yang baru dibuat beserta `id_siswa`.
    """
    try:
        # Mengubah model Pydantic menjadi dictionary untuk dikirim ke Supabase
        data_to_insert = siswa_data.dict()
        data_to_insert['tanggal_lahir'] = data_to_insert['tanggal_lahir'].isoformat()

        # Eksekusi perintah INSERT ke tabel 'siswa'
        response = supabase.table("siswa").insert(data_to_insert).execute()

        # Jika Supabase tidak mengembalikan data, berarti ada masalah
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gagal menambahkan siswa. Periksa kembali data Anda."
            )

        # Mengembalikan data siswa yang baru saja dibuat
        return response.data[0]

    except Exception as e:
        # Menangani kemungkinan error duplikat (misal: NISN/NIK sudah ada)
        # atau error database lainnya.
        if "duplicate key value violates unique constraint" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Gagal menambahkan siswa. NISN, NIK, atau data unik lainnya sudah terdaftar."
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan pada server: {str(e)}"
        )

# ===========================================================================
# Statistik
# ===========================================================================

@app.get(
    "/statistik/pendaftaran",
    response_model=StatistikPendaftaranResponse,
    tags=["Statistik"],
    summary="Dapatkan Statistik Pendaftaran",
    description="Mengambil data agregat seperti jumlah pendaftar, rata-rata nilai, dan rata-rata peringkat dari semua pendaftar."
)
async def get_statistik_pendaftaran():
    """
    Memanggil fungsi RPC `get_statistik_pendaftaran` di Supabase untuk
    mendapatkan data agregat secara efisien.
    """
    try:
        # Panggil RPC function yang sudah dibuat di Supabase
        response = supabase.rpc("get_statistik_pendaftaran").execute()

        # RPC akan mengembalikan list dengan satu dictionary di dalamnya
        if not response.data:
            # Ini terjadi jika fungsi RPC tidak mengembalikan baris, misal karena error tak terduga
            # atau jika tabel kosong, COUNT akan 0 dan AVG akan NULL.
            return StatistikPendaftaranResponse(jumlah_pendaftar=0, rerata_nilai=0.0, rerata_peringkat=0.0)

        stats_data = response.data[0]

        # Konversi nilai ke float, tangani kasus NULL dari AVG jika tabel kosong
        rerata_nilai_val = float(stats_data.get('rerata_nilai') or 0)
        rerata_peringkat_val = float(stats_data.get('rerata_peringkat') or 0)

        return StatistikPendaftaranResponse(
            jumlah_pendaftar=stats_data.get('jumlah_pendaftar', 0),
            rerata_nilai=round(rerata_nilai_val, 2),
            rerata_peringkat=round(rerata_peringkat_val, 2)
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan saat mengambil statistik: {str(e)}"
        )

# ===========================================================================
# Periode Beasiswa
# ===========================================================================
@app.patch(
    "/periode/{id_periode}/publish",
    tags=["Periode Beasiswa"],
    summary="Update Status Publikasi Periode",
    description="Mengubah status 'is_publish' sebuah periode beasiswa menjadi true atau false."
)
async def update_publish_status(id_periode: int, publish_data: PublishStatusUpdate):
    """
    Endpoint untuk mengupdate status `is_publish` dari sebuah periode beasiswa.

    - **id_periode**: ID dari periode yang akan diupdate.
    - **Request Body**: `{"is_publish": true}` atau `{"is_publish": false}`.
    """
    try:
        # Update kolom 'is_publish' di tabel 'periode_beasiswa'
        response = supabase.table("periode_beasiswa") \
            .update({"is_publish": publish_data.is_publish}) \
            .eq("id_periode", id_periode) \
            .execute()

        # Jika tidak ada baris yang diupdate, berarti ID tidak ditemukan
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Periode beasiswa dengan ID {id_periode} tidak ditemukan."
            )

        return {
            "message": "Status publikasi berhasil diperbarui.",
            "data": response.data[0]
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan pada server: {str(e)}"
        )

    # --- ENDPOINT API (Baru) ---
@app.get(
    "/periode/{id_periode}/is-publish",
    response_model=IsPublishResponse,
    tags=["Periode Beasiswa"],
    summary="Cek Status Publikasi Periode",
    description="Mendapatkan status 'is_publish' (true atau false) dari sebuah periode beasiswa."
)
async def check_is_publish(id_periode: int):
    """
    Endpoint untuk mengecek status `is_publish` dari sebuah periode beasiswa.

    - **id_periode**: ID dari periode yang akan diperiksa.
    """
    try:
        # Ambil hanya kolom 'is_publish' untuk efisiensi
        response = supabase.table("periode_beasiswa") \
            .select("is_publish") \
            .eq("id_periode", id_periode) \
            .maybe_single() \
            .execute()

        # Jika tidak ada record yang ditemukan
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Periode beasiswa dengan ID {id_periode} tidak ditemukan."
            )

        # Kembalikan data dalam format yang sesuai dengan model respons
        return IsPublishResponse(is_publish=response.data['is_publish'])

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan pada server: {str(e)}"
        )