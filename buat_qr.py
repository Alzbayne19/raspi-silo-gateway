import qrcode

# Data dummy masih simpel buat testing, bisa diganti sesuai kebutuhan
daftar_qr = [
    ("qr_batch_001.png", "BATCH-001|MATERIAL-A|2026-11-20"),
    ("qr_batch_002.png", "BATCH-002|MATERIAL-B|2026-11-20"),
]

for nama_file, isi_data in daftar_qr:
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4,
    )
    qr.add_data(isi_data)
    qr.make(fit=True)

    gambar = qr.make_image(fill_color="black", back_color="white")
    gambar.save(nama_file)
    print(f"[OK] Berhasil membuat {nama_file} -> Isi: {isi_data}")