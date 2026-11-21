"""Genera un'anteprima statica e cliccabile del gestionale (pagine reali + dati di prova).

Uso:  python strumenti/anteprima.py <cartella_uscita>
Le pagine sono quelle vere del programma, salvate come HTML; i link interni puntano
alle pagine salvate; i salvataggi sono disattivati. Gli Excel per la contabile sono
mostrati come griglia, con le formule già calcolate.
"""
import io
from html import escape as html_escape
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import override_settings  # noqa: E402

from asta.models import Asta, Lotto, Paletta  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "anteprima")
FUORI = 'data-anteprima-off="1"'

INIEZIONE = """
<div id="anteprima-bar" style="position:sticky;top:0;z-index:9999;background:#1d2b3a;color:#fff;font:13px/1.4 -apple-system,Segoe UI,Arial,sans-serif;padding:8px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap">
  <span style="font-weight:600">ANTEPRIMA</span>
  <span style="opacity:.8">dati inventati · i pulsanti che salvano non sono attivi · {ruolo}</span>
  <a href="novita.html" style="color:#ffd9a0;font-weight:600;text-decoration:none;margin-left:auto">Novità e domande aperte →</a>
</div>
<div id="anteprima-toast" hidden style="position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#1d2b3a;color:#fff;padding:10px 16px;border-radius:8px;font:14px -apple-system,Segoe UI,Arial,sans-serif;z-index:10000;max-width:90vw"></div>
<script>
(function(){
  var t=document.getElementById('anteprima-toast'), h;
  function avvisa(m){ t.textContent=m; t.hidden=false; clearTimeout(h); h=setTimeout(function(){t.hidden=true},3200); }
  document.addEventListener('click',function(e){
    var a=e.target.closest('a[data-anteprima-off]');
    if(a){ e.preventDefault(); avvisa('Questa pagina non è inclusa nell\\'anteprima.'); }
  },true);
  // pagina di aggiudicazione: nell'anteprima il salvataggio è simulato
  if (/sala-\\d+\\.html/.test(location.pathname)) {
    window.fetch = function(url, opt){
      var risposta = {lotti: []};
      if (opt && opt.method === 'POST') {
        var d = JSON.parse(opt.body), tr = document.querySelector('tr[data-id="'+d.lotto+'"]');
        var prezzo = String(d.prezzo||'').replace(/\\./g,'').replace(',', '.');
        var stato = d.azione === 'invenduto' ? 'invenduto' : (d.azione === 'annulla' ? 'in_asta' : 'aggiudicato');
        risposta = {ok: true, msg: 'ANTEPRIMA (non salvato): lotto ' + tr.dataset.numero + (stato==='aggiudicato' ? ' → paletta ' + d.paletta + ' a € ' + d.prezzo : (stato==='invenduto' ? ': invenduto' : ': annullato')),
                    lotto: {id: d.lotto, numero: tr.dataset.numero, stato: stato, paletta: stato==='aggiudicato' ? d.paletta : null,
                            prezzo: stato==='aggiudicato' ? prezzo : null, sotto_riserva: false}};
      }
      return Promise.resolve({json: function(){ return Promise.resolve(risposta); }});
    };
  }
  document.addEventListener('submit',function(e){ e.preventDefault(); avvisa('Nell\\'anteprima i salvataggi non sono attivi: nel programma vero questo pulsante salva.'); },true);
})();
</script>
"""


def nome_file(path: str) -> str | None:
    """URL del programma -> nome del file nell'anteprima (None = non incluso)."""
    u = urlsplit(path)
    p = u.path
    if p == "/palette/":
        m = re.search(r"asta=(\d+)", u.query)
        return f"palette-{m.group(1)}.html" if m else "palette.html"
    regole = [
        (r"^/$", "index.html"),
        (r"^/aste/$", "aste.html"),
        (r"^/anagrafica/$", "anagrafica.html"),
        (r"^/anagrafica/nuova/$", "scheda-nuova.html"),
        (r"^/anagrafica/(\d+)/$", "scheda-{0}.html"),
        (r"^/aste/nuova/$", "asta-nuova.html"),
        (r"^/lotti/modello-excel/$", "excel-modello-lotti.html"),
        (r"^/asta/(\d+)/$", "cruscotto-{0}.html"),
        (r"^/asta/(\d+)/modifica/$", "asta-modifica-{0}.html"),
        (r"^/asta/(\d+)/faldone/$", "excel-faldone-{0}.html"),
        (r"^/asta/(\d+)/lotti/$", "lotti-{0}.html"),
        (r"^/asta/(\d+)/lotti/importa/$", "lotti-importa-{0}.html"),
        (r"^/asta/(\d+)/lotti/riproponi/$", "riproponi-{0}.html"),
        (r"^/asta/(\d+)/sala/$", "sala-{0}.html"),
        (r"^/asta/(\d+)/post-asta/$", "post-asta-{0}.html"),
        (r"^/asta/(\d+)/acquirenti/$", "acquirenti-{0}.html"),
        (r"^/asta/(\d+)/acquirenti/(\d+)/email/$", "email-{0}-{1}.html"),
        (r"^/asta/(\d+)/acquirenti/(\d+)/conteggio\.pdf$", "conteggio-{0}-{1}.html"),
        (r"^/asta/(\d+)/acquirenti/conteggi\.pdf$", "conteggi-{0}.html"),
        (r"^/asta/(\d+)/mandanti/$", "mandanti-{0}.html"),
        (r"^/asta/(\d+)/mandanti/rendiconti\.pdf$", "conteggi-mandanti-{0}.html"),
        (r"^/asta/(\d+)/mandanti/(\d+)/rendiconto\.pdf$", "conteggio-mandante-{0}-{1}.html"),
        (r"^/asta/(\d+)/mandanti/(\d+)/email/$", "email-mandante-{0}-{1}.html"),
        (r"^/asta/(\d+)/aggiudicazioni/$", "aggiudicazioni-{0}.html"),
        (r"^/asta/(\d+)/invenduti/$", "invenduti-{0}.html"),
        (r"^/asta/(\d+)/incassi/$", "incassi-{0}.html"),
        (r"^/asta/(\d+)/pagamenti-mandanti/$", "pagamenti-mandanti-{0}.html"),
        (r"^/asta/(\d+)/excel/incassi/$", "excel-incassi-{0}.html"),
        (r"^/asta/(\d+)/excel/mandanti/$", "excel-mandanti-{0}.html"),
        (r"^/asta/(\d+)/paletta/(\d+)/conto/$", "conto-{0}-{1}.html"),
        (r"^/asta/(\d+)/paletta/(\d+)/spedizione/$", "spedizione-{0}-{1}.html"),
        (r"^/asta/(\d+)/mandante/(\d+)/rendiconto/$", "rendiconto-{0}-{1}.html"),
        (r"^/admin/$", "archivio.html"),
        (r"^/admin/asta/paletta/$", "archivio-palette.html"),
        (r"^/admin/asta/lotto/$", "archivio-lotti.html"),
        (r"^/admin/asta/soggetto/$", "archivio-anagrafiche.html"),
        (r"^/admin/asta/mandato/$", "archivio-mandati.html"),
        (r"^/admin/asta/incasso/$", "archivio-incassi.html"),
        (r"^/admin/asta/incasso/add/$", "incasso-nuovo.html"),
        (r"^/admin/asta/pagamentomandante/$", "archivio-pagamenti.html"),
        (r"^/admin/asta/pagamentomandante/add/$", "pagamento-nuovo.html"),
    ]
    for rx, nome in regole:
        m = re.match(rx, p)
        if m:
            return nome.format(*m.groups())
    return None


def riscrivi(html: str, ruolo: str) -> tuple[str, set]:
    trovati = set()

    def link(m):
        attr, q, url = m.group(1), m.group(2), m.group(3)
        if url.startswith(("http", "#", "mailto:", "javascript:", "data:")):
            return m.group(0)
        if url.startswith("/static/"):
            return f'{attr}={q}{url[1:]}{q}'
        if url.startswith("/admin/jsi18n/"):
            return f'{attr}={q}static/jsi18n.js{q}'
        if url.startswith("/"):
            f = nome_file(url)
            if f:
                trovati.add(url)
                return f'{attr}={q}{f}{q}'
            return f'{attr}={q}#{q} {FUORI}'
        return m.group(0)

    html = re.sub(r'(href|src|action)=(["\'])([^"\']*)\2', link, html)
    html = html.replace("&amp;", "&") if False else html
    html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + INIEZIONE.replace("{ruolo}", ruolo), html, count=1)
    html = html.replace('onclick="window.print()"', 'onclick="document.getElementById(\'anteprima-toast\').textContent=\'Nel programma vero apre la stampa / PDF.\';document.getElementById(\'anteprima-toast\').hidden=false"')
    return html, trovati


# ---------------------------------------------------------------- Excel -> HTML

def _valuta(ws):
    """Calcola le formule semplici usate nei nostri Excel (somme, SUM, SUMIF, ROUND)."""
    from openpyxl.utils import column_index_from_string, get_column_letter
    vals = {}

    def val(ref):
        ref = ref.replace("$", "")
        if ref in vals:
            return vals[ref]
        v = ws[ref].value
        if isinstance(v, str) and v.startswith("="):
            v = calc(v[1:])
        vals[ref] = v
        return v

    def rng(a, b):
        ca, ra = re.match(r"([A-Z]+)(\d+)", a).groups()
        cb, rb = re.match(r"([A-Z]+)(\d+)", b).groups()
        out = []
        for c in range(column_index_from_string(ca), column_index_from_string(cb) + 1):
            for r in range(int(ra), int(rb) + 1):
                out.append(f"{get_column_letter(c)}{r}")
        return out

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    def calc(f):
        f = re.sub(r'SUMIF\(([A-Z]+\d+):([A-Z]+\d+),"<>",([A-Z]+\d+):([A-Z]+\d+)\)',
                   lambda m: repr(sum(num(val(y)) for x, y in zip(rng(m[1], m[2]), rng(m[3], m[4]))
                                      if val(x) not in (None, ""))), f)
        f = re.sub(r"SUM\(([A-Z]+\d+):([A-Z]+\d+)\)", lambda m: repr(sum(num(val(c)) for c in rng(m[1], m[2]))), f)
        f = f.replace("ROUND(", "round(")
        f = re.sub(r"\b([A-Z]{1,2}\d+)\b", lambda m: repr(num(val(m[1]))), f)
        try:
            return eval(f, {"__builtins__": {}, "round": round})
        except Exception:
            return f"={f}"

    return val


def excel_html(xlsx: bytes, titolo: str, spiegazione: str) -> str:
    from datetime import datetime

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    wb = load_workbook(io.BytesIO(xlsx))
    parti = []
    for ws in wb.worksheets:
        val = _valuta(ws)
        ncol, nrow = ws.max_column, ws.max_row
        righe = ['<tr><th class="hd"></th>' + "".join(
            f'<th class="hd" style="min-width:{(ws.column_dimensions[get_column_letter(c)].width or 9) * 7:.0f}px">'
            f'{get_column_letter(c)}</th>' for c in range(1, ncol + 1)) + "</tr>"]
        for r in range(1, nrow + 1):
            celle = []
            for c in range(1, ncol + 1):
                cell = ws.cell(r, c)
                v = val(cell.coordinate)
                fmt = cell.number_format or ""
                if isinstance(v, datetime):
                    txt = v.strftime("%d/%m/%y")
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and ("0.00" in fmt):
                    txt = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    txt = ("€ " + txt) if "€" in fmt and txt else txt
                    if v == 0 and "-" in fmt:
                        txt = "-"
                else:
                    txt = "" if v is None else str(v)
                stile = []
                if cell.fill.fill_type and cell.fill.fgColor.type == "rgb":
                    stile.append(f"background:#{cell.fill.fgColor.rgb[-6:]}")
                elif cell.fill.fill_type:
                    stile.append("background:#ddebf7")
                if cell.font.bold:
                    stile.append("font-weight:700")
                if cell.font.color is not None and cell.font.color.type == "rgb" and cell.font.color.rgb not in (None, "FF000000"):
                    stile.append(f"color:#{cell.font.color.rgb[-6:]}")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    stile.append("text-align:right")
                formula = ws.cell(r, c).value
                tip = f' title="{html_escape(formula)}"' if isinstance(formula, str) and formula.startswith("=") else ""
                celle.append(f'<td style="{";".join(stile)}"{tip}>{txt.replace(chr(10), "<br>")}</td>')
            righe.append(f'<tr><th class="hd">{r}</th>{"".join(celle)}</tr>')
        parti.append(f'<h2>Foglio «{ws.title}»</h2><div class="xl"><table>{"".join(righe)}</table></div>')
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titolo}</title><style>
body{{margin:0;font:14px/1.45 -apple-system,Segoe UI,Arial,sans-serif;background:#f4f5f7;color:#1d1d1f}}
main{{padding:16px 20px 40px}} h1{{font-size:20px;margin:8px 0 4px}} h2{{font-size:15px;margin:22px 0 8px}} p{{max-width:760px;color:#555}}
.xl{{overflow-x:auto;background:#fff;border:1px solid #c9ccd1}}
.xl table{{border-collapse:collapse;font:12px Calibri,Arial,sans-serif}}
.xl td,.xl th{{border:1px solid #e1e3e6;padding:3px 6px;white-space:nowrap;vertical-align:top}}
.xl th.hd{{background:#eef0f2;color:#666;font-weight:400;text-align:center;position:sticky;top:0}}
.xl td[title]{{cursor:help}}
</style></head><body><main><h1>{titolo}</h1><p>{spiegazione}</p>{"".join(parti)}</main></body></html>"""


def pdf_come_pagina(pdf: bytes, nome: str, out: Path) -> str:
    """Nell'anteprima i PDF non si scaricano: li mostriamo come immagini delle pagine."""
    import subprocess
    import tempfile
    (out / "img").mkdir(exist_ok=True)
    base = nome.removesuffix(".html")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "d.pdf").write_bytes(pdf)
        subprocess.run(["pdftoppm", "-r", "90", "-png", str(Path(tmp) / "d.pdf"), str(Path(tmp) / "p")], check=True)
        immagini = sorted(Path(tmp).glob("p-*.png"))[:6]
        nomi = []
        for i, im in enumerate(immagini, start=1):
            dest = out / "img" / f"{base}-{i}.png"
            shutil.copy(im, dest)
            nomi.append(f"img/{dest.name}")
    pagine = "".join(f'<img src="{n}" alt="pagina {i}" style="width:100%;max-width:760px;display:block;margin:0 auto 18px;'
                     f'box-shadow:0 2px 12px rgba(0,0,0,.15);background:#fff">' for i, n in enumerate(nomi, start=1))
    altre = "" if len(nomi) < 6 else '<p style="text-align:center;color:#666">… (nell\'anteprima sono mostrate le prime 6 pagine)</p>'
    return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Conteggio aggiudicazioni</title></head><body style="margin:0;background:#e9eaec;font:14px -apple-system,Segoe UI,Arial">'
            f'<main style="padding:20px 16px"><p style="max-width:760px;margin:0 auto 14px;color:#444">Così appare il PDF del conteggio '
            f'(nel programma si apre o si scarica; è anche allegato all\'email per Outlook).</p>{pagine}{altre}</main></body></html>')


def _file_esempio():
    """Catalogo Word inventato, nello stesso formato delle «Schede Proprietà»."""
    import docx
    from django.core.files.uploadedfile import SimpleUploadedFile
    d = docx.Document()
    def sezione(t):
        d.add_paragraph().add_run(t).bold = True
    def lotto(titolo, righe, prezzo):
        d.add_paragraph(f"00. {titolo}")
        for r in righe:
            d.add_paragraph(r)
        if prezzo:
            d.add_paragraph(prezzo)
        d.add_paragraph("")
    sezione("TAVOLE ORIGINALI")
    sezione("AVVENTURA ANNI ‘70")
    lotto("Corto Maltese: Favola di Venezia", ["Tavola originale a china, 1977. Cm 36x50. Firmata. Ottimo stato."],
          "€ 1.500/3.000 R 1.800 BIANCHI")
    lotto("Sgt. Kirk", ["Coppia di tavole originali, 1970. Cm 34x48.", "Qualità: A-."], "€ 400/800 BIANCHI DA FOTOGRAFARE")
    lotto("Mister No", ["Copertina originale a tempera, 1979. Cm 30x40."], "€ 600/1.200 ROMANO")
    sezione("MANIFESTI E LOCANDINE")
    lotto("La Guerra dei Mondi", ["Locandina in prima edizione italiana, 1954. Cm 68x30,5."], "€ 80/175 GALLERIA COLOMBO")
    lotto("Il Pianeta Proibito", ["Fotobusta in prima edizione italiana, 1956. Cm 34x50. Qualità: B+."],
          "€ 40/80 ROSSETTI")
    lotto("Barbarella", ["Locandina, 1968. Grafica di Symeoni."], "€")
    sezione("PROSSIME ASTE O VENDITA DIRETTA")
    lotto("Valentina", ["Serigrafia firmata, 1985."], "€ 100/200")
    buf = io.BytesIO()
    d.save(buf)
    return SimpleUploadedFile("Schede_Proprieta_AT94.docx", buf.getvalue())


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "static").mkdir(parents=True)
    asta = Asta.objects.filter(chiusa=False, data__lte=__import__("datetime").date.today()).order_by("-data").first()
    admin = User.objects.get(username="admin")
    clerk = User.objects.get(username="clerk")

    with override_settings(ALLOWED_HOSTS=["*"], DEBUG=True, CASA_ASTA_IBAN="IT00 X000 0000 0000 0000 0000 000 (esempio)",
                           CASA_ASTA_EMAIL="aste@example.com", CASA_ASTA_FIRMA="Casa d'Aste\nUfficio aste"):
        c_admin, c_clerk = Client(), Client()
        c_admin.force_login(admin)
        c_clerk.force_login(clerk)
        lotti_modificabili = set(Lotto.objects.filter(asta__chiusa=False, asta__data__gt=asta.data)
                                 .values_list("pk", flat=True))
        da_fare = ["/", f"/asta/{asta.pk}/", "/admin/", "/admin/asta/paletta/", "/admin/asta/lotto/",
                   "/admin/asta/soggetto/", "/admin/asta/mandato/", "/admin/asta/incasso/",
                   "/admin/asta/pagamentomandante/"]
        speciali = {  # pagine da scaricare con parametri d'esempio
            "/admin/asta/incasso/add/": "/admin/asta/incasso/add/?paletta={}&importo=250.00".format(
                asta.palette.order_by("numero").first().pk),
            "/admin/asta/pagamentomandante/add/": f"/admin/asta/pagamentomandante/add/?asta={asta.pk}",
        }
        fatti, fatti_file = set(), set()
        while da_fare:
            url = da_fare.pop(0)
            chiave = urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).path == "/palette/" and "asta=" in url else "")
            f = nome_file(url)
            if not f or chiave in fatti or f in fatti_file:
                continue
            if f.startswith("lotto-") and int(re.findall(r"\d+", f)[0]) not in lotti_modificabili:
                continue
            fatti.add(chiave)
            if f.startswith("excel-"):
                from asta.export_contabile import excel_contabile
                if f == "excel-modello-lotti.html":
                    xl = c_admin.get("/lotti/modello-excel/").content
                    html = excel_html(xl, "Modello Excel per caricare i lotti",
                                      "Il file che si scarica da «Scarica il modello Excel»: si compila una riga per lotto "
                                      "e si carica dalla pagina Lotti dell'asta.")
                    html, _ = riscrivi(html, "modello")
                    (OUT / f).write_text(html, encoding="utf-8")
                    continue
                asta_f = Asta.objects.get(pk=int(re.findall(r"\d+", f)[0]))
                if f.startswith("excel-faldone-"):
                    from asta.faldone import crea_faldone
                    html = excel_html(crea_faldone(asta_f), f"Faldone asta {asta_f.numero}",
                                      "Il file che si scarica da «Scarica il faldone». Stessi fogli del vostro: "
                                      "Foglio1 (tutti i dati), DAPHNE (gestione dell'asta), SERGIO (battitore, con le "
                                      "righe di cambio tornata), un foglio per ogni tornata, FAX (registrazione delle "
                                      "aggiudicazioni) e ASSICURAZIONE.")
                    html, _ = riscrivi(html, "faldone")
                    (OUT / f).write_text(html, encoding="utf-8")
                    continue
                if "incassi" in f:
                    html = excel_html(excel_contabile(asta_f, "incassi"), "Excel incassi per la contabile",
                                      "Così appare il file scaricato dal pulsante «Excel incassi»: stesse colonne del "
                                      "vostro foglio, più alcune colonne a destra. Verde = incassato, giallo = pagato "
                                      "in parte. Passando sopra una cifra calcolata si vede la formula.")
                else:
                    html = excel_html(excel_contabile(asta_f, "mandanti"), "Excel pagamenti ai mandanti",
                                      "Così appare il file del pulsante «Excel pagamenti»: verde = pagato, bianco = "
                                      "ancora da pagare, come nel vostro foglio. Gli acconti hanno una riga «DA PAGARE» "
                                      "con il residuo.")
                html, _ = riscrivi(html, "vista contabile")
                (OUT / f).write_text(html, encoding="utf-8")
                continue
            if f.startswith(("conteggio-", "conteggi-")):
                html = pdf_come_pagina(c_admin.get(urlsplit(url).path).content, f, OUT)
                html, _ = riscrivi(html, "documento PDF (qui come immagine)")
                (OUT / f).write_text(html, encoding="utf-8")
                continue
            client, ruolo = (c_clerk, "vista del clerk di sala (nessun dato personale)") if f.startswith("sala-") \
                else (c_admin, "vista amministrazione")
            if f.startswith("lotti-importa-") and Asta.objects.get(pk=int(re.findall(r"\d+", f)[0])).lotti.exists():
                r = c_admin.post(url, {"file": _file_esempio()})
            else:
                r = client.get(speciali.get(chiave, url))
            if r.status_code != 200:
                print("!!", url, r.status_code)
                continue
            html, nuovi = riscrivi(r.content.decode("utf-8"), ruolo)
            (OUT / f).write_text(html, encoding="utf-8")
            da_fare += [u for u in nuovi if nome_file(u) and nome_file(u) not in fatti_file]
            fatti_file.add(f)
        js = c_admin.get("/admin/jsi18n/")
        (OUT / "static" / "jsi18n.js").write_bytes(js.content)

    # file statici dell'archivio (CSS, icone, script)
    import django.contrib.admin as adm
    shutil.copytree(Path(adm.__file__).parent / "static" / "admin", OUT / "static" / "admin",
                    ignore=shutil.ignore_patterns("*.map", "LICENSE*", "*.md", "i18n", "xregexp"))
    # select2 usa file di lingua: teniamo solo l'italiano
    i18n = Path(adm.__file__).parent / "static/admin/js/vendor/select2/i18n"
    (OUT / "static/admin/js/vendor/select2/i18n").mkdir(parents=True, exist_ok=True)
    shutil.copy(i18n / "it.js", OUT / "static/admin/js/vendor/select2/i18n/it.js")
    novita = (Path(__file__).parent / "anteprima_indice.html").read_text(encoding="utf-8")
    novita, _ = riscrivi(novita.replace("<body>", "<body>") if "<body" in novita else
                         "<!doctype html><html lang=it><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'></head><body>" + novita + "</body></html>",
                         "novità e domande")
    (OUT / "novita.html").write_text(novita, encoding="utf-8")
    n = len(list(OUT.glob("*.html")))
    print(f"Anteprima generata in {OUT}: {n} pagine")


if __name__ == "__main__":
    main()
