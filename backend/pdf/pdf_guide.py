import io
from xml.sax.saxutils import escape
from reportlab.lib import colors as C
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader
from backend.design import template as t
from backend.design import colors, engine
from backend.design import presentation
from backend.pdf.pdf_technical import PT, _fonts, _ring, _transform, _safe_text, STYLES
from backend.design import step_catalog

INK=C.HexColor('#1E2430'); BLUE=C.HexColor('#28418F'); CREAM=C.HexColor('#FAF6EF'); GRAY=C.HexColor('#394150')

class NumberedCanvas(canvas.Canvas):
    def __init__(self,*args,revision='',**kwargs):
        super().__init__(*args,**kwargs);self.states=[];self.revision=revision
    def showPage(self):
        self.states.append(dict(self.__dict__));self._startPage()
    def save(self):
        count=len(self.states)
        for state in self.states:
            self.__dict__.update(state);self.setFillColor(GRAY);self.setFont('SISA-Jakarta',8)
            self.drawString(15*PT,10*PT,'SISA · Pouch kamu');self.drawRightString(195*PT,10*PT,f'Hal {self._pageNumber} / {count} · rev {self.revision[:8]}')
            self.setFont('SISA-Jakarta',8);self.drawCentredString(105*PT,5*PT,'Versi uji coba - rakit dulu dari kertas sebelum memotong kain.')
            super().showPage()
        super().save()

class Guide:
    def __init__(self,pdf,data,candidate,illustration=None):
        self.p=pdf;self.data=data;self.c=candidate;self.font,self.bold=_fonts();self.y=265
        self.materials={m['id']:m for m in data['materials']};self.placements={p['instance_id']:p for p in candidate['placements']}
        self.illustration=illustration
    def line(self,text,x,y,size=11,bold=False,color=INK):
        font=self.bold if bold else self.font
        self.p.setFillColor(color);self.p.setFont(font,size);self.p.drawString(x*PT,y*PT,_safe_text(text,font))
    def para(self,text,x,y,width=180,size=11,bold=False):
        style=ParagraphStyle('body',fontName=self.bold if bold else self.font,fontSize=size,leading=size*1.5,textColor=INK)
        para=Paragraph(escape(_safe_text(text,self.bold if bold else self.font)),style);_,height=para.wrap(width*PT,400*PT);para.drawOn(self.p,x*PT,y*PT-height);return y-height/PT
    def page(self,title,kicker='PETUNJUK POUCH'):
        self.line(kicker,15,279,8,True,BLUE);self.line(title,15,269,18,True);self.y=259
    def end(self):self.p.showPage()
    def box(self,text,x,y,w,h):
        self.p.setFillColor(CREAM);self.p.setStrokeColor(C.HexColor('#D6CBB9'));self.p.roundRect(x*PT,(y-h)*PT,w*PT,h*PT,4*PT,fill=1,stroke=1);self.para(text,x+5,y-5,w-10,11)
    def pouch(self,x,y,width,height):
        front=[self.materials[p['material_id']] for p in self.c['placements'] if p['instance_id'].startswith('FRONT')]
        p=self.p;p.saveState();p.setLineWidth(.8)
        for i,m in enumerate(front):p.setFillColor(C.HexColor(colors.resolve(m['color'])['hex'] or '#8D9097'));p.rect((x+i*width/len(front))*PT,y*PT,width/len(front)*PT,height*PT,fill=1,stroke=0)
        p.setStrokeColor(INK);p.rect(x*PT,y*PT,width*PT,height*PT,fill=0,stroke=1)
        p.setDash(3,3);p.line((x+3)*PT,(y+height-5)*PT,(x+width-3)*PT,(y+height-5)*PT)
        if len(front)==2:p.line((x+width/2)*PT,y*PT,(x+width/2)*PT,(y+height-5)*PT)
        p.setDash();p.restoreState()
    def calibration(self,x,y):
        self.p.setStrokeColor(INK);self.p.setLineWidth(.8);self.p.setDash();self.p.rect(x*PT,y*PT,50*PT,50*PT);self.line('Cek: 50 × 50 mm',x+4,y+25,9,True);self.line('Ukur kedua sisi.',x+4,y+19,8)

def cover(g):
    g.page('Pouch kamu','SISA · DARI KAIN SISAMU');g.line('Petunjuk jahit',15,254,24,True)
    r=g.data['request'];g.line(f'Ukuran jadi {r["W_mm"]/10:g} × {r["H_mm"]/10:g} cm',15,239,14)
    scale=min(100/r['W_mm'],90/r['H_mm']);w,h=r['W_mm']*scale,r['H_mm']*scale
    if g.illustration:
        g.p.drawImage(ImageReader(io.BytesIO(g.illustration)),15*PT,133*PT,180*PT,94*PT,preserveAspectRatio=True,anchor='c',mask='auto')
        g.line('Ilustrasi AI (gaya foto); warna bisa sedikit berbeda. Ikuti swatch kain, bukan gambar untuk ukuran pola.',15,122,9)
    else:
        g.pouch((210-w)/2,133,w,h);g.line('Ilustrasi bentuk; kain asli bisa jatuh dan menggembung berbeda. Bukan ukuran pola.',15,122,9)
    y=110
    for placement in g.c['placements']:
        m=g.materials[placement['material_id']];g.p.setFillColor(C.HexColor(colors.resolve(m['color'])['hex'] or '#8D9097'));g.p.rect(15*PT,(y-2)*PT,5*PT,5*PT,fill=1,stroke=0)
        y=g.para(presentation.PART_NAMES[placement['instance_id']]+' · '+presentation.fabric_name(m),24,y+3,171,11)-3
    g.line('Kamu butuh',15,y-3,14,True);g.para('Printer A4, penggaris, gunting, klip atau jarum pentul, selotip atau lem kertas. Untuk uji kain setelah ditinjau: mesin jahit atau jarum dan benang.',15,y-10,180,11);g.end()

def before(g):
    g.page('Sebelum mulai');g.line('1. Cetak ukuran asli',15,251,14,True);g.para('Pilih Actual size / 100%. Matikan Fit to page. Pola harus dicetak pada kertas A4 tanpa penyesuaian ukuran.',15,242)
    g.box('UKURAN ASLI 100%  |  Sesuaikan ke halaman: MATI',15,220,180,19)
    g.line('2. Periksa kotak dengan penggaris',15,192,14,True);g.calibration(15,132);g.para(f'Ukur lebar dan tinggi kotak. Target masing-masing 50 mm; selisih paling banyak {engine.CONFIG["printed_square_mm"]:g} mm. Jika meleset, hentikan dan perbaiki pengaturan cetak.',75,181,120)
    g.line('Hasil ukur: lebar ........ mm · tinggi ........ mm',75,139,9)
    g.line('3. Kenali garis pada pola',15,119,14,True)
    for i,(kind,label) in enumerate([('cut','Potong'),('sew','Jahit'),('fold','Lipat'),('hem','Jahit lipatan')]):
        y=106-i*10;color,dash,weight=STYLES[kind];g.p.setStrokeColor(color);g.p.setDash(dash);g.p.setLineWidth(weight);g.p.line(15*PT,y*PT,45*PT,y*PT);g.line(label,50,y-1,11)
    g.p.setDash();g.para('Kampuh: jarak jahit dari tepi. Panah: arah serat kain. Tanda segitiga: cocokkan posisi dua potongan.',15,65,180,11)
    g.box('Panduan ini versi uji coba dan konstruksinya belum ditinjau penjahit. Rakit dulu dari kertas. Jangan gunakan sebagai bukti kapasitas atau kekuatan.',15,38,180,22);g.end()

def layout(g):
    g.page('Potong dari kain mana?');g.para('Susunan potongan (gambar diperkecil, bukan pola 1:1). Letakkan muka kain menghadap atas. Jangan dibalik.',15,260,180,11)
    selected=[m for m in g.data['materials'] if any(p['material_id']==m['id'] for p in g.c['placements'])]
    maxw=max(max(x for x,y in m['outer_mm']) for m in g.data['materials']);maxh=max(max(y for x,y in m['outer_mm']) for m in g.data['materials']);scale=min(72/maxw,55/maxh)
    parts={p['id']:p for p in g.c['patterns']}
    for i,m in enumerate(selected):
        x=15+(i%2)*94;top=227-(i//2)*78;oy=top-55;project=lambda ring:[((x+px*scale)*PT,(oy+py*scale)*PT) for px,py in ring]
        g.line(presentation.fabric_name(m),x,top+4,9,True);g.p.setStrokeColor(INK);g.p.setFillColor(C.HexColor(colors.resolve(m['color'])['hex'] or '#8D9097'));g.p.setFillAlpha(.18);_ring(g.p,project(m['outer_mm']),fill=1);g.p.setFillAlpha(1)
        for d in m['defects_mm']:
            g.p.setFillColor(C.HexColor('#B62F24'));_ring(g.p,project(d),fill=1);g.line('hindari',x,oy-5,8)
        for pl in g.c['placements']:
            if pl['material_id']!=m['id']:continue
            p=parts[pl['instance_id']];g.p.setFillColor(C.white);g.p.setStrokeColor(BLUE);ring=_transform(p['cut_mm'],pl);_ring(g.p,project(ring),fill=1);cx=sum(v[0] for v in ring)/len(ring);cy=sum(v[1] for v in ring)/len(ring);g.line(presentation.PART_NAMES[p['id']],x+cx*scale-13,oy+cy*scale,8)
    y=227-((len(selected)+1)//2)*78-5;g.line('Bagian · ukuran potong · kain · jumlah',15,y,11,True);y-=10
    for p in g.c['patterns']:
        m=g.materials[g.placements[p['id']]['material_id']];text=f'{presentation.PART_NAMES[p["id"]]} · {p["width_mm"]/10:g} × {p["height_mm"]/10:g} cm ({p["width_mm"]:g} × {p["height_mm"]:g} mm) · {presentation.fabric_name(m)} · 1'
        y=g.para(text,15,y,180,10)-5
    g.end()

def pattern(g,p,index):
    m=g.materials[g.placements[p['id']]['material_id']]
    g.line(f'Pola {index} · {presentation.PART_NAMES[p["id"]]} · POLA 1:1',15,278,14,True)
    g.line(f'CETAK 100% · {p["width_mm"]/10:g} × {p["height_mm"]/10:g} cm · '+presentation.fabric_name(m),15,272.5,9)
    ox,oy=15,70;w,h=p['width_mm'],p['height_mm'];pdf=g.p
    pdf.saveState();pdf.setStrokeColor(INK);pdf.setLineWidth(.8);pdf.rect(ox*PT,oy*PT,w*PT,h*PT)
    for kind,key in [('sew','sew_paths'),('fold','fold_paths'),('hem','hem_paths')]:
        color,dash,weight=STYLES[kind];pdf.setStrokeColor(color);pdf.setDash(dash);pdf.setLineWidth(weight)
        for path in p[key]:
            pts=path['points_mm'];v=pdf.beginPath();v.moveTo((ox+pts[0][0])*PT,(oy+pts[0][1])*PT)
            for x,y in pts[1:]:v.lineTo((ox+x)*PT,(oy+y)*PT)
            pdf.drawPath(v)
    pdf.setDash();pdf.setStrokeColor(INK)
    for x,y in p['notches_mm']:
        direction=1 if y==0 else -1;pdf.line((ox+x-1.4)*PT,(oy+y)*PT,(ox+x)*PT,(oy+y+direction*2.5)*PT);pdf.line((ox+x)*PT,(oy+y+direction*2.5)*PT,(ox+x+1.4)*PT,(oy+y)*PT)
    # Labels stay inside pattern; the numerical paths themselves are unaltered.
    g.line('lipat '+f'{t.HEM_FOLD_1_MM:g}'+' mm',ox+4,oy+h-4,8)
    g.line('lipat '+f'{t.HEM_FOLD_2_MM:g}'+' mm lagi',ox+4,oy+h-9,8)
    g.line('jahit lipatan',ox+4,oy+h-17,8)
    g.line(presentation.PART_NAMES[p['id']],ox+14,oy+h*.65,14,True)
    g.line(f'Lebar potong {w:g} mm',ox+14,oy+h*.65-7,10)
    g.line(f'Tinggi potong {h:g} mm',ox+14,oy+h*.65-14,10)
    cy=oy+h*.27;cx=ox+w/2;horizontal=w>=h;dx,dy=(15,0) if horizontal else (0,15)
    pdf.line((cx-dx)*PT,(cy-dy)*PT,(cx+dx)*PT,(cy+dy)*PT)
    for sign in [-1,1]:
        ex,ey=cx+sign*dx,cy+sign*dy;ux,uy=sign*dx/15,sign*dy/15
        for side in [-1,1]:pdf.line(ex*PT,ey*PT,(ex-3*ux+side*1.5*uy)*PT,(ey-3*uy-side*1.5*ux)*PT)
    g.line('arah serat',ox+14,cy-abs(dy)-6,9);g.line(f'jahit di sini · {t.S:g} mm dari tepi',ox+14,oy+5,8)
    pdf.restoreState();g.calibration(15,15);g.para('Garis luar = potong. Garis putus = jahit. Cocokkan tanda segitiga. Ukur lebar dan tinggi panel setelah mencetak; jangan mengubah skala.',75,57,120,10)
    g.para(f'Selisih cetak panel paling banyak {engine.CONFIG["printed_panel_mm"]:g} mm. Kotak: {engine.CONFIG["printed_square_mm"]:g} mm. Hentikan jika tidak sesuai.',75,34,120,9);g.end()

def diagram_geometry(data,candidate,operation):
    """Instruction drawings use actual template paths, scaled only for the card."""
    if operation=='print':
        return {'width_mm':210,'height_mm':297,'parts':[]}
    if operation=='measure':
        return {'width_mm':data['request']['W_mm'],'height_mm':data['request']['H_mm'],'parts':[]}
    if operation=='join':
        parts=[part for part in candidate['patterns'] if part['id'].startswith('FRONT_')]
        return {'width_mm':sum(p['width_mm'] for p in parts)+4,'height_mm':parts[0]['height_mm'],'parts':parts}
    part=next(p for p in candidate['patterns'] if p['id']=='BACK')
    return {'width_mm':part['width_mm'],'height_mm':part['height_mm'],'parts':[part]}

def diagram(g,operation,x,y,width=45,height=33):
    geometry=diagram_geometry(g.data,g.c,operation)
    scale=min(width/geometry['width_mm'],height/geometry['height_mm'])
    actual_width=geometry['width_mm']*scale;actual_height=geometry['height_mm']*scale
    p=g.p;p.saveState();p.setFillColor(CREAM);p.setStrokeColor(INK);p.setLineWidth(.8)
    def path(points,offset=0,kind='sew'):
        color,dash,weight=STYLES[kind];p.setStrokeColor(color);p.setDash(dash);p.setLineWidth(weight)
        line=p.beginPath();line.moveTo((x+(points[0][0]+offset)*scale)*PT,(y+points[0][1]*scale)*PT)
        for px,py in points[1:]:line.lineTo((x+(px+offset)*scale)*PT,(y+py*scale)*PT)
        p.drawPath(line)
    def rect(px,w,h):
        p.setDash();p.setStrokeColor(INK);p.setFillColor(CREAM);p.rect((x+px*scale)*PT,y*PT,w*scale*PT,h*scale*PT,fill=1,stroke=1)
    if operation=='print':
        rect(0,210,297);p.rect((x+25*scale)*PT,(y+35*scale)*PT,50*scale*PT,50*scale*PT);g.line('50 mm',x+actual_width+2,y+4,8)
    elif operation=='join':
        offset=0
        for part in geometry['parts']:
            rect(offset,part['width_mm'],part['height_mm'])
            seam=next(line for line in part['sew_paths'] if line['id']=='JOIN_FRONT')
            path(seam['points_mm'],offset)
            for px,py in part['notches_mm']:
                direction=1 if py==0 else -1
                path([[px-1.4,py],[px,py+direction*2.5],[px+1.4,py]],offset,'cut')
            offset+=part['width_mm']+4
        g.line('Sisi bagus saling menghadap',x,y-5,8)
    elif operation=='measure':
        rect(0,geometry['width_mm'],geometry['height_mm'])
        g.line(f"{g.data['request']['W_mm']/10:g} × {g.data['request']['H_mm']/10:g} cm",x,y-5,8)
    else:
        part=geometry['parts'][0];rect(0,part['width_mm'],part['height_mm'])
        keys=['fold_paths','hem_paths'] if operation=='hem' else ['sew_paths']
        for key in keys:
            for line in part[key]:path(line['points_mm'],kind='fold' if key=='fold_paths' else 'hem' if key=='hem_paths' else 'sew')
        if operation=='hem':
            g.line('Lipat ke sisi dalam',x,y-5,8)
        elif operation=='corners':
            p.setDash();p.setStrokeColor(INK);p.circle((x+t.S*scale)*PT,(y+t.S*scale)*PT,min(2.5,t.S*scale)*PT);g.line('Periksa; belum ada finishing',x,y-5,8)
        else:g.line('Bagian atas tetap terbuka',x,y-5,8)
    p.restoreState()

def instructions(g):
    steps=step_catalog.steps(g.data,g.c)
    for start in range(0,len(steps),3):
        g.page('Langkah demi langkah')
        for row,step in enumerate(steps[start:start+3]):
            top=253-row*78;g.line(str(start+row+1),15,top,22,True,BLUE);g.line(step['title'],28,top,14,True)
            diagram(g,step['diagram_id'],15,top-48)
            g.para(step['text'],67,top-9,128,11)
            g.p.setStrokeColor(INK);g.p.rect(15*PT,(top-66)*PT,3*PT,3*PT);g.para('Cek: '+step['check'],21,top-61,174,9)
        g.end()

def final(g):
    g.page('Selesai! Cek hasilmu');r=g.data['request'];scale=min(100/r['W_mm'],83/r['H_mm']);w,h=r['W_mm']*scale,r['H_mm']*scale;g.pouch((210-w)/2,170,w,h);g.line(f'Target {r["W_mm"]/10:g} × {r["H_mm"]/10:g} cm',15,157,18,True)
    g.para(f'Selisih ukuran luar paling banyak {engine.CONFIG["sewn_exterior_mm"]:g} mm. Ini batas pemeriksaan prototipe, bukan bukti pouch akan muat atau kuat.',15,147)
    for y,text in [(118,'Tanggal: ........................................................'),(100,'Hasil ukur: lebar ........ cm × tinggi ........ cm'),(82,'Catatan sudut dan lipatan:'),(58,'........................................................................................'),(40,'........................................................................................')]:g.line(text,15,y,11)
    g.end()

def generate_pdf(data,candidate,signature_val,mode='paper_test',illustration=None):
    _fonts();buffer=io.BytesIO();pdf=NumberedCanvas(buffer,pagesize=A4,pageCompression=1,revision=candidate['design_revision_id']);pdf.setTitle('Pouch kamu - Petunjuk jahit');pdf.setAuthor('SISA');g=Guide(pdf,data,candidate,illustration)
    cover(g);before(g);layout(g)
    for i,p in enumerate(candidate['patterns'],1):pattern(g,p,i)
    instructions(g);final(g);pdf.save();return buffer.getvalue()
