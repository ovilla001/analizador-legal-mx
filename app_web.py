"""
=============================================================================
ANALIZADOR LEGAL — INTERFAZ WEB LOCAL
=============================================================================
Ejecuta:  python app_web.py
Abre:     http://localhost:5000
=============================================================================
"""

import os, sys, base64, csv, re, time, secrets
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, send_file, render_template_string, session, redirect, url_for

# ── Verificar dependencias ───────────────────────────────────────────────────
FALTANTES = []
try:    import anthropic
except: FALTANTES.append("anthropic")
try:    import fitz
except: FALTANTES.append("PyMuPDF")
try:    import pandas as pd
except: FALTANTES.append("pandas")
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except: FALTANTES.append("openpyxl")
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_OK = True
except:
    PDF2IMAGE_OK = False
try:
    from docx import Document as DocxDoc
    DOCX_OK = True
except:
    DOCX_OK = False

if FALTANTES:
    print(f"\n❌ Instala: pip install {' '.join(FALTANTES)}")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────────────
app             = Flask(__name__)
app.secret_key  = os.environ.get("SECRET_KEY", secrets.token_hex(32))
UPLOAD_DIR      = Path("uploads_web")
OUTPUT_DIR      = Path("resultados_legales")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
MAX_PAGINAS     = 30
MODELO          = "claude-opus-4-6"
APP_PASSWORD    = os.environ.get("APP_PASSWORD", "")   # Vacío = sin protección

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if APP_PASSWORD and not session.get("autenticado"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

SYSTEM_PROMPT = """Eres un experto en Derecho Corporativo Mexicano con especialización en revisión de Libros Sociales y Actas Constitutivas, con pericia en auditoría de cumplimiento para el registro REPSE.

Tu tarea es analizar el documento adjunto y extraer la información necesaria para el Registro Maestro de la sociedad.

INSTRUCCIONES:
1. Determina el tipo de documento (Constitución, Asamblea Ordinaria, Extraordinaria o Sesión de Consejo).
2. Localiza las cláusulas que impacten los rubros de la tabla.
3. Si es una modificación, identifica el estado anterior y el nuevo estado.
4. Genera ESTRICTAMENTE una tabla Markdown con columnas: | Categoría | Dato Actualizado / Cláusula | Observaciones / Cambios Clave |

CAMPOS OBLIGATORIOS:
- Tipo de Documento
- Denominación Social
- Domicilio Social
- Objeto Social
- Capital Social (fijo, variable y total)
- Generales de los Socios (entrada/salida)
- Tenencia Accionaria (por socio)
- Órganos de Administración
- Facultades / Poderes
- Comisario
- Duración de la Sociedad
- Fecha del Acta
- Notaría / Fedatario
- Observaciones REPSE

RESTRICCIONES:
- NO inventes datos. Si un campo no aparece, escribe: "Sin cambios en este documento"
- Si hay sección ilegible: "Sección ilegible - verificar documento original"
- Responde SOLO con la tabla Markdown, sin texto adicional."""

# ── Funciones de extracción ──────────────────────────────────────────────────
def extraer_texto_pdf(ruta):
    doc = fitz.open(str(ruta))
    texto = "".join(doc[i].get_text() for i in range(min(len(doc), MAX_PAGINAS)))
    doc.close()
    return texto.strip(), len(texto.strip()) < 100

def pdf_a_imagenes(ruta):
    if not PDF2IMAGE_OK: return []
    paginas = convert_from_path(str(ruta), dpi=200, first_page=1, last_page=MAX_PAGINAS)
    imgs = []
    for i, p in enumerate(paginas):
        tmp = UPLOAD_DIR / f"_tmp_{i}.png"
        p.save(str(tmp), "PNG")
        with open(tmp, "rb") as f:
            imgs.append({"type":"image","source":{"type":"base64","media_type":"image/png","data":base64.b64encode(f.read()).decode()}})
        tmp.unlink(missing_ok=True)
    return imgs

def extraer_texto_docx(ruta):
    if not DOCX_OK: return ""
    doc = DocxDoc(str(ruta))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

def analizar(cliente, texto="", imagenes=None):
    if imagenes:
        contenido = imagenes + [{"type":"text","text":"Analiza estas páginas del acta y genera la tabla Markdown con TODOS los campos obligatorios."}]
    else:
        contenido = [{"type":"text","text":f"Analiza este documento legal y genera la tabla:\n\n---\n{texto[:150000]}\n---"}]
    r = cliente.messages.create(model=MODELO, max_tokens=4096, system=SYSTEM_PROMPT,
                                messages=[{"role":"user","content":contenido}])
    return r.content[0].text

def parsear_tabla(md):
    filas = []
    for linea in md.splitlines():
        linea = linea.strip()
        if not linea.startswith("|"): continue
        if re.match(r"^\|[-\s|]+\|$", linea): continue
        if "Categoría" in linea or "Categoria" in linea: continue
        celdas = [c.strip() for c in linea.split("|") if c.strip()]
        if len(celdas) >= 2:
            filas.append({"cat": celdas[0], "dato": celdas[1], "obs": celdas[2] if len(celdas)>2 else ""})
    return filas

def guardar_excel(filas, nombre, tipo):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook(); ws = wb.active; ws.title = "Registro Maestro"
    borde = Border(**{s:Side(style="thin",color="AAAAAA") for s in ["left","right","top","bottom"]})

    def c(row, col, val, bold=False, bg=None, fg="000000", sz=10, wrap=True, ha="left"):
        cell = ws.cell(row=row, column=col, value=val)
        cell.font = Font(name="Arial", bold=bold, size=sz, color=fg)
        cell.alignment = Alignment(horizontal=ha, vertical="top", wrap_text=wrap)
        cell.border = borde
        if bg: cell.fill = PatternFill("solid", fgColor=bg)

    ws.merge_cells("A1:C1")
    cell = ws.cell(row=1, column=1, value=f"REGISTRO MAESTRO — {nombre.upper()}")
    cell.font = Font(name="Arial", bold=True, size=13, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="1F3864")
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = borde
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:C2")
    cell2 = ws.cell(row=2, column=1, value=f"Tipo: {tipo}  |  Procesado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    cell2.font = Font(name="Arial", size=9, color="FFFFFF", italic=True)
    cell2.fill = PatternFill("solid", fgColor="2E75B6")
    cell2.alignment = Alignment(horizontal="center", vertical="center")
    cell2.border = borde
    ws.row_dimensions[2].height = 16

    for col, enc in enumerate(["CATEGORÍA","DATO ACTUALIZADO / CLÁUSULA","OBSERVACIONES / CAMBIOS CLAVE"], 1):
        c(3, col, enc, bold=True, bg="1F3864", fg="FFFFFF", sz=10, ha="center")
    ws.row_dimensions[3].height = 20

    for i, f in enumerate(filas):
        row = i + 4
        bg = "FFF2CC" if "Capital" in f["cat"] else ("FFE0B2" if "REPSE" in f["cat"] else ("E2EFDA" if "Administración" in f["cat"] or "Poderes" in f["cat"] or "Facultades" in f["cat"] else ("F2F2F2" if i%2==0 else "FFFFFF")))
        c(row, 1, f["cat"],  bold=True,  bg=bg, sz=10)
        c(row, 2, f["dato"], bold=False, bg=bg, sz=10)
        c(row, 3, f["obs"],  bold=False, bg=bg, sz=10)
        ws.row_dimensions[row].height = 80

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 60
    ws.freeze_panes = "A4"

    ruta = OUTPUT_DIR / f"Registro_{nombre.replace('.','_').replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    wb.save(str(ruta))
    return ruta

def guardar_csv(filas, nombre, tipo):
    ruta = OUTPUT_DIR / f"Registro_{nombre.replace('.','_').replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Fecha","Archivo","Tipo","Categoría","Dato Actualizado / Cláusula","Observaciones"])
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        for row in filas:
            w.writerow([fecha, nombre, tipo, row["cat"], row["dato"], row["obs"]])
    return ruta

# ── HTML de la interfaz ──────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Analizador Legal — Actas Corporativas MX</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;min-height:100vh}
  header{background:linear-gradient(135deg,#1F3864,#2E75B6);color:#fff;padding:28px 40px;box-shadow:0 2px 8px rgba(0,0,0,.3)}
  header h1{font-size:1.7rem;letter-spacing:.5px}
  header p{margin-top:6px;font-size:.9rem;opacity:.85}
  .badge{display:inline-block;background:#FFF2CC;color:#7B6000;border-radius:20px;padding:3px 12px;font-size:.78rem;font-weight:600;margin-top:8px}
  main{max-width:960px;margin:36px auto;padding:0 20px}

  .card{background:#fff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.08);margin-bottom:28px;overflow:hidden}
  .card-header{background:#1F3864;color:#fff;padding:16px 24px;font-size:1rem;font-weight:600;display:flex;align-items:center;gap:10px}
  .card-body{padding:24px}

  .drop-zone{border:2.5px dashed #2E75B6;border-radius:10px;padding:48px 24px;text-align:center;cursor:pointer;transition:.2s;background:#f7faff}
  .drop-zone:hover,.drop-zone.drag{border-color:#1F3864;background:#e8f0fb}
  .drop-zone .icon{font-size:3rem;margin-bottom:12px}
  .drop-zone p{color:#2E75B6;font-weight:600;font-size:1.05rem}
  .drop-zone small{color:#888;margin-top:6px;display:block}
  #file-name{margin-top:14px;font-size:.9rem;color:#1F3864;font-weight:600;display:none}

  .api-row{display:flex;gap:12px;align-items:center;margin-top:18px}
  .api-row input{flex:1;padding:11px 16px;border:1.5px solid #ccd;border-radius:8px;font-size:.95rem;font-family:monospace}
  .api-row input:focus{outline:none;border-color:#2E75B6}

  button.primary{background:linear-gradient(135deg,#1F3864,#2E75B6);color:#fff;border:none;padding:13px 32px;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;transition:.2s;width:100%;margin-top:18px}
  button.primary:hover{opacity:.9;transform:translateY(-1px)}
  button.primary:disabled{opacity:.5;cursor:not-allowed;transform:none}

  .progress{display:none;margin-top:18px}
  .progress-bar{height:6px;background:#e0e8f5;border-radius:3px;overflow:hidden}
  .progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#2E75B6,#1F3864);border-radius:3px;transition:width .4s}
  .progress-text{font-size:.85rem;color:#2E75B6;margin-top:8px;font-weight:500}

  .result-area{display:none}
  .alert{padding:12px 18px;border-radius:8px;margin-bottom:16px;font-size:.9rem}
  .alert.success{background:#E2EFDA;color:#1a5e20;border-left:4px solid #4CAF50}
  .alert.error{background:#FDECEA;color:#b71c1c;border-left:4px solid #f44336}
  .alert.warn{background:#FFF8E1;color:#7B6000;border-left:4px solid #FFC107}

  table{width:100%;border-collapse:collapse;font-size:.88rem;margin-top:12px}
  th{background:#1F3864;color:#fff;padding:11px 14px;text-align:left;font-size:.82rem;text-transform:uppercase;letter-spacing:.5px}
  td{padding:10px 14px;vertical-align:top;border-bottom:1px solid #e8eef5;line-height:1.5}
  tr:nth-child(even) td{background:#f7faff}
  td:first-child{font-weight:600;color:#1F3864;white-space:nowrap;min-width:160px}
  td.capital{background:#FFF9E6!important}
  td.repse{background:#FFF3E0!important}
  td.admin{background:#F1F8E9!important}

  .dl-bar{display:flex;gap:12px;margin-top:18px}
  .dl-btn{flex:1;padding:11px;border-radius:8px;border:2px solid #2E75B6;background:#fff;color:#2E75B6;font-weight:600;cursor:pointer;font-size:.9rem;transition:.2s;text-align:center;text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px}
  .dl-btn:hover{background:#2E75B6;color:#fff}

  .steps{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:4px}
  .step{text-align:center;padding:16px 10px}
  .step .n{width:36px;height:36px;border-radius:50%;background:#2E75B6;color:#fff;font-weight:700;font-size:1rem;display:flex;align-items:center;justify-content:center;margin:0 auto 10px}
  .step p{font-size:.85rem;color:#555;line-height:1.5}

  .tag{display:inline-block;background:#e8f0fb;color:#1F3864;border-radius:4px;padding:2px 8px;font-size:.76rem;font-weight:600;margin-right:4px}

  footer{text-align:center;padding:28px;color:#aaa;font-size:.8rem}
</style>
</head>
<body>
<header>
  <h1>⚖️ Analizador Legal de Actas Corporativas</h1>
  <p>Derecho Corporativo Mexicano — Extracción automática para Registro Maestro de Sociedades</p>
  <span class="badge">🤖 Powered by Claude (Anthropic)</span>
</header>

<main>
  <!-- Cómo funciona -->
  <div class="card">
    <div class="card-header">📋 ¿Cómo funciona?</div>
    <div class="card-body">
      <div class="steps">
        <div class="step"><div class="n">1</div><p>Sube tu <strong>PDF o DOCX</strong><br>del acta (escaneado o digital)</p></div>
        <div class="step"><div class="n">2</div><p>Claude analiza el documento como <strong>experto legal</strong></p></div>
        <div class="step"><div class="n">3</div><p>Descarga tu <strong>Registro Maestro</strong> en Excel y CSV</p></div>
      </div>
      <p style="font-size:.82rem;color:#888;margin-top:12px;text-align:center">
        Soporta: <span class="tag">Actas Constitutivas</span> <span class="tag">Asambleas Ordinarias</span> <span class="tag">Asambleas Extraordinarias</span> <span class="tag">Sesiones de Consejo</span> <span class="tag">PDFs escaneados</span>
      </p>
    </div>
  </div>

  <!-- Formulario -->
  <div class="card">
    <div class="card-header">📤 Subir Documento</div>
    <div class="card-body">
      <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
        <div class="icon">📄</div>
        <p>Arrastra tu acta aquí o haz clic para seleccionar</p>
        <small>PDF o DOCX · Máximo 50 MB · Hasta 30 páginas</small>
      </div>
      <input type="file" id="fileInput" accept=".pdf,.docx" style="display:none" onchange="onFileSelect(this)">
      <div id="file-name">📎 <span id="fn"></span></div>

      <div class="api-row">
        <input type="password" id="apiKey" placeholder="sk-ant-api03-... (tu Anthropic API Key)">
      </div>

      <button class="primary" id="btnAnalizar" onclick="analizar()" disabled>
        🔍 Analizar Documento
      </button>

      <div class="progress" id="progress">
        <div class="progress-bar"><div class="progress-fill" id="pFill"></div></div>
        <div class="progress-text" id="pText">Iniciando análisis...</div>
      </div>
    </div>
  </div>

  <!-- Resultados -->
  <div class="card result-area" id="resultArea">
    <div class="card-header">✅ Registro Maestro Extraído</div>
    <div class="card-body">
      <div id="alertBox"></div>
      <table id="resultTable"><thead><tr><th>Categoría</th><th>Dato Actualizado / Cláusula</th><th>Observaciones / Cambios Clave</th></tr></thead><tbody id="resultBody"></tbody></table>
      <div class="dl-bar" id="dlBar" style="display:none">
        <a class="dl-btn" id="dlExcel" href="#" download>📊 Descargar Excel (.xlsx)</a>
        <a class="dl-btn" id="dlCsv" href="#" download>📋 Descargar CSV</a>
      </div>
    </div>
  </div>
</main>
<footer>Uso interno · Documentos procesados de forma local · API de Anthropic · © 2025</footer>

<script>
let selectedFile = null;

const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('drag');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

function onFileSelect(input) { if (input.files[0]) setFile(input.files[0]); }
function setFile(f) {
  selectedFile = f;
  document.getElementById('fn').textContent = f.name + ' (' + (f.size/1024/1024).toFixed(2) + ' MB)';
  document.getElementById('file-name').style.display = 'block';
  checkReady();
}
document.getElementById('apiKey').addEventListener('input', checkReady);
function checkReady() {
  const ok = selectedFile && document.getElementById('apiKey').value.trim().startsWith('sk-ant');
  document.getElementById('btnAnalizar').disabled = !ok;
}

function setProgress(pct, txt) {
  document.getElementById('pFill').style.width = pct + '%';
  document.getElementById('pText').textContent = txt;
}

async function analizar() {
  const apiKey = document.getElementById('apiKey').value.trim();
  const btn = document.getElementById('btnAnalizar');
  btn.disabled = true;
  document.getElementById('progress').style.display = 'block';
  document.getElementById('resultArea').style.display = 'none';

  const steps = [
    [15, '📄 Leyendo el documento...'],
    [35, '🖼️ Procesando páginas (puede tomar un momento)...'],
    [60, '🤖 Claude analizando las cláusulas legales...'],
    [85, '📊 Estructurando el Registro Maestro...'],
    [100, '✅ Análisis completado'],
  ];
  let si = 0;
  const ticker = setInterval(() => {
    if (si < steps.length - 1) { setProgress(...steps[si]); si++; }
  }, 4000);

  try {
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('api_key', apiKey);

    const res = await fetch('/analizar', { method: 'POST', body: fd });
    const data = await res.json();
    clearInterval(ticker);
    setProgress(100, '✅ ¡Listo!');

    if (data.error) {
      showAlert('error', '❌ ' + data.error);
    } else {
      renderTable(data.filas);
      if (data.excel_url) {
        document.getElementById('dlExcel').href = data.excel_url;
        document.getElementById('dlCsv').href = data.csv_url;
        document.getElementById('dlBar').style.display = 'flex';
      }
    }
    document.getElementById('resultArea').style.display = 'block';
  } catch(e) {
    clearInterval(ticker);
    showAlert('error', '❌ Error de conexión: ' + e.message);
    document.getElementById('resultArea').style.display = 'block';
  }
  btn.disabled = false;
}

function showAlert(type, msg) {
  document.getElementById('alertBox').innerHTML = `<div class="alert ${type}">${msg}</div>`;
}

function renderTable(filas) {
  const tbody = document.getElementById('resultBody');
  tbody.innerHTML = '';
  document.getElementById('alertBox').innerHTML = '';
  filas.forEach(f => {
    const tr = document.createElement('tr');
    const isCapital = f.cat.includes('Capital');
    const isRepse   = f.cat.includes('REPSE');
    const isAdmin   = f.cat.includes('Administración') || f.cat.includes('Facultad') || f.cat.includes('Poder');
    const cls = isCapital ? 'capital' : isRepse ? 'repse' : isAdmin ? 'admin' : '';
    tr.innerHTML = `<td class="${cls}">${f.cat}</td><td class="${cls}">${f.dato.replace(/\\n/g,'<br>')}</td><td class="${cls}">${f.obs.replace(/\\n/g,'<br>')}</td>`;
    tbody.appendChild(tr);
  });
}
</script>
</body>
</html>"""

HTML_LOGIN = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acceso — Analizador Legal MX</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(135deg,#1F3864,#2E75B6);min-height:100vh;display:flex;align-items:center;justify-content:center}
  .box{background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.25);padding:44px 40px;width:100%;max-width:400px;text-align:center}
  .logo{font-size:2.8rem;margin-bottom:10px}
  h1{color:#1F3864;font-size:1.3rem;margin-bottom:6px}
  p{color:#888;font-size:.88rem;margin-bottom:28px}
  input{width:100%;padding:13px 16px;border:1.5px solid #dde;border-radius:8px;font-size:1rem;font-family:monospace;margin-bottom:16px;outline:none;transition:.2s}
  input:focus{border-color:#2E75B6}
  button{width:100%;padding:13px;background:linear-gradient(135deg,#1F3864,#2E75B6);color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;transition:.2s}
  button:hover{opacity:.9}
  .error{background:#FDECEA;color:#b71c1c;border-radius:8px;padding:10px 14px;font-size:.88rem;margin-bottom:16px;text-align:left}
  footer{margin-top:22px;font-size:.76rem;color:#bbb}
</style>
</head>
<body>
<div class="box">
  <div class="logo">⚖️</div>
  <h1>Analizador Legal de Actas</h1>
  <p>Derecho Corporativo Mexicano<br>Acceso restringido — solo personal autorizado</p>
  {% if error %}<div class="error">❌ Contraseña incorrecta. Intenta de nuevo.</div>{% endif %}
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Contraseña de acceso" autofocus required>
    <button type="submit">Entrar →</button>
  </form>
  <footer>Powered by Claude (Anthropic)</footer>
</div>
</body>
</html>"""

# ── Rutas ────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
def login():
    error = False
    if request.method == "POST":
        pwd = request.form.get("password","")
        if pwd == APP_PASSWORD:
            session["autenticado"] = True
            return redirect(url_for("index"))
        error = True
    return render_template_string(HTML_LOGIN, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template_string(HTML)

@app.route("/analizar", methods=["POST"])
@login_required
def analizar_endpoint():
    try:
        api_key = request.form.get("api_key","").strip()
        if not api_key:
            return jsonify({"error":"API Key no proporcionada."}), 400

        f = request.files.get("file")
        if not f:
            return jsonify({"error":"No se recibió ningún archivo."}), 400

        nombre = f.filename
        ext    = Path(nombre).suffix.lower()
        ruta   = UPLOAD_DIR / nombre
        f.save(str(ruta))

        cliente = anthropic.Anthropic(api_key=api_key)

        texto, es_escaneo, imagenes = "", False, []
        if ext == ".pdf":
            texto, es_escaneo = extraer_texto_pdf(ruta)
            if es_escaneo:
                imagenes = pdf_a_imagenes(ruta)
                if not imagenes:
                    return jsonify({"error":"PDF escaneado sin soporte de pdf2image. Instala poppler."}), 400
        elif ext == ".docx":
            texto = extraer_texto_docx(ruta)
        else:
            return jsonify({"error":"Formato no soportado. Usa PDF o DOCX."}), 400

        tabla_md = analizar(cliente, texto=texto, imagenes=imagenes if es_escaneo else None)
        filas    = parsear_tabla(tabla_md)

        if not filas:
            return jsonify({"error":"No se pudo extraer información del documento. Verifica la calidad del archivo."}), 400

        tipo = next((f["dato"] for f in filas if "Tipo" in f["cat"]), "No identificado")

        excel_path = guardar_excel(filas, nombre, tipo)
        csv_path   = guardar_csv(filas, nombre, tipo)

        return jsonify({
            "filas":     filas,
            "tipo":      tipo,
            "excel_url": f"/descargar/{excel_path.name}",
            "csv_url":   f"/descargar/{csv_path.name}",
        })

    except anthropic.AuthenticationError:
        return jsonify({"error":"API Key inválida. Verifica tu clave de Anthropic."}), 401
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/descargar/<nombre>")
@login_required
def descargar(nombre):
    ruta = OUTPUT_DIR / nombre
    if ruta.exists():
        return send_file(str(ruta), as_attachment=True)
    return "Archivo no encontrado", 404

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    api_env = os.environ.get("ANTHROPIC_API_KEY","")
    print("\n" + "="*55)
    print("  ⚖️  ANALIZADOR LEGAL — INTERFAZ WEB")
    print("="*55)
    if api_env:
        print(f"  ✅ ANTHROPIC_API_KEY detectada en el entorno.")
    else:
        print("  ℹ️  Puedes ingresar tu API Key directamente en la web.")
    print(f"  🌐 Corriendo en puerto: {port}")
    print("  🛑 Para detener:        Ctrl + C")
    print("="*55 + "\n")
    app.run(debug=debug, host="0.0.0.0", port=port)
