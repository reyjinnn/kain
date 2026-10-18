from backend.design import template as t
from backend.design import engine

CATALOG = {
 1: ("Cetak dan cek skala", "Cetak pola pada ukuran asli (100%). Ukur kedua sisi kotak: masing-masing harus 50 mm. Untuk uji kertas, gunakan selotip sebagai pengganti jahitan.", "Kotak sesuai; selisih paling banyak {print_tol} mm.", "print"),
 2: ("Sambung dua potongan depan", "Hadapkan sisi bagus kedua potongan depan. Cocokkan tanda segitiga, lalu sambungkan pada garis jahit dengan jarak {join_mm} mm dari tepi. Hasilnya satu panel depan.", "Panel gabungan {raw_W_cm} × {raw_H_cm} cm sebelum dilipat.", "join"),
 3: ("Lipat dan jahit tepi atas", "Lipat tepi atas {fold1_mm} mm ke sisi yang nanti ada di dalam. Lipat lagi {fold2_mm} mm, lalu jahit sepanjang garis jahit lipatan. Lakukan pada depan dan belakang.", "Tepi atas sejajar dan bukaan tidak tertutup.", "hem"),
 4: ("Satukan depan dan belakang", "Tumpuk depan dan belakang dengan sisi bagus saling menghadap. Jahit kiri, bawah, dan kanan pada garis {seam_mm} mm dari tepi. Biarkan bagian atas terbuka.", "Tiga sisi tersambung; atas tetap terbuka.", "sew"),
 5: ("Periksa sudut", "Cara merapikan sudut belum ditetapkan. Untuk versi uji kertas, periksa pertemuan garis dan catat bagian yang perlu ditinjau sebelum memakai kain.", "Catatan sudut sudah diisi; finishing menunggu tinjauan.", "corners"),
 6: ("Balik dan ukur", "Balik ke sisi bagus dan ratakan tanpa menarik. Ukur hasil: target {W_cm} × {H_cm} cm, selisih paling banyak {tol_mm} mm. Catat hasilmu di halaman terakhir.", "Ukuran dicatat dan dibandingkan dengan target.", "measure"),
}

def steps(data, candidate):
    r=data["request"]
    values={"W_cm":f'{r["W_mm"]/10:g}', "H_cm":f'{r["H_mm"]/10:g}',
            "raw_W_cm":f'{(r["W_mm"]+2*t.S)/10:g}', "raw_H_cm":f'{(r["H_mm"]+t.S+t.H)/10:g}',
            "seam_mm":f'{t.S:g}', "join_mm":f'{t.J:g}', "fold1_mm":f'{t.HEM_FOLD_1_MM:g}', "fold2_mm":f'{t.HEM_FOLD_2_MM:g}',
            "tol_mm":f'{engine.CONFIG["sewn_exterior_mm"]:g}', "print_tol":f'{engine.CONFIG["printed_square_mm"]:g}'}
    return [{"id":step["id"], "title":CATALOG[step["id"]][0], "text":CATALOG[step["id"]][1].format(**values),
             "check":CATALOG[step["id"]][2].format(**values), "diagram_id":CATALOG[step["id"]][3]} for step in candidate["assembly_steps"]]
