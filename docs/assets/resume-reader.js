/* Reads a resume or job description file in the browser and prepares the text that is sent.
 * The file itself never leaves the device; only the prepared text goes to the API.
 *
 *   NNResume.readFile(file)   -> { text, kind, pages }      PDF (pdf.js), DOCX (mammoth), TXT/MD
 *   NNResume.prepare(text)    -> { text, sections, roles, years, span, removed }
 *
 * prepare() splits the text into sections by their headings, reads date ranges in the
 * experience section (roles, years of experience, first and last year), removes emails,
 * phone numbers and links, and keeps only the sections that carry skills. Libraries load
 * only when a file is chosen, from pinned cdnjs URLs checked against SHA-384 hashes.
 */
(function () {
  "use strict";

  const LIBS = {
    pdf: { url: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js",
           sri: "sha384-/1qUCSGwTur9vjf/z9lmu/eCUYbpOTgSjmpbMQZ1/CtX2v/WcAIKqRv+U1DUCG6e" },
    pdfWorker: { url: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js",
                 sri: "sha384-SnzOobpRMLXZ52iJvZm/C0fYw0OQemTXzTjIsdsfMcrCtCEe9qgzxTd3RSklO5x2" },
    docx: { url: "https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.8.0/mammoth.browser.min.js",
            sri: "sha384-/cXAMbzovUIKbBERjPmR3SnPTh8siWr5lsvFYj1Uq4XP0yaJUZJmsh0YXyGv5P0y" },
  };
  const MAX_BYTES = 2 * 1024 * 1024;
  const MAX_CHARS = 12000;            // the API's limit (app/models/schemas.py PUBLIC_MAX_TEXT)

  /* ── loading the libraries ── */
  const loaded = {};
  function script({ url, sri }) {
    return loaded[url] ||= new Promise((resolve, reject) => {
      const s = document.createElement("script");
      Object.assign(s, { src: url, integrity: sri, crossOrigin: "anonymous", referrerPolicy: "no-referrer" });
      s.onload = resolve; s.onerror = () => reject(new Error(`could not load ${url}`));
      document.head.append(s);
    });
  }
  async function verifiedBlobUrl({ url, sri }) {      // the pdf.js worker cannot take an integrity attribute
    const bytes = await (await fetch(url, { mode: "cors", referrerPolicy: "no-referrer" })).arrayBuffer();
    const digest = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest("SHA-384", bytes))));
    if (`sha384-${digest}` !== sri) throw new Error("the PDF reader failed its integrity check");
    return URL.createObjectURL(new Blob([bytes], { type: "text/javascript" }));
  }

  /* ── files to text ── */
  async function readPdf(file) {
    await script(LIBS.pdf);
    const lib = window.pdfjsLib;
    lib.GlobalWorkerOptions.workerSrc ||= await verifiedBlobUrl(LIBS.pdfWorker);
    const doc = await lib.getDocument({ data: await file.arrayBuffer(), isEvalSupported: false }).promise;
    const pages = [];
    for (let p = 1; p <= doc.numPages; p++) {
      const content = await (await doc.getPage(p)).getTextContent();
      pages.push(linesFromItems(content.items));
    }
    return { text: pages.join("\n"), kind: "pdf", pages: doc.numPages };
  }
  // pdf.js returns positioned text runs; rebuild lines by their baseline, left to right
  function linesFromItems(items) {
    const rows = [];
    for (const it of items) {
      if (!it.str || !it.str.trim()) continue;
      const y = it.transform[5], x = it.transform[4];
      let row = rows.find(r => Math.abs(r.y - y) < 3);
      if (!row) rows.push(row = { y, parts: [] });
      row.parts.push({ x, s: it.str });
    }
    return rows.sort((a, b) => b.y - a.y)
      .map(r => r.parts.sort((a, b) => a.x - b.x).map(p => p.s).join(" ").replace(/\s+/g, " ").trim())
      .filter(Boolean).join("\n");
  }
  async function readDocx(file) {
    await script(LIBS.docx);
    const { value } = await window.mammoth.extractRawText({ arrayBuffer: await file.arrayBuffer() });
    return { text: value, kind: "docx", pages: null };
  }
  async function readFile(file) {
    if (file.size > MAX_BYTES) throw new Error("This file is over 2 MB. Paste the text instead.");
    const name = file.name.toLowerCase();
    const reader = name.endsWith(".pdf") ? readPdf : name.endsWith(".docx") ? readDocx
      : /\.(txt|md|text)$/.test(name) ? async f => ({ text: await f.text(), kind: "text", pages: null }) : null;
    if (!reader) throw new Error("Use a PDF, DOCX or TXT file, or paste the text.");
    let out;
    try { out = await reader(file); }
    catch (e) {
      if (/load|integrity/.test(e.message)) throw new Error(`The file reader could not start (${e.message}). Paste the text instead.`);
      throw new Error("This file could not be read (it may be damaged or password-protected). Paste the text instead.");
    }
    if (out.text.replace(/\s/g, "").length < 40)
      throw new Error("No text found in this file (a scanned PDF is an image). Paste the text instead.");
    return out;
  }

  /* ── sections, dates, redaction ── */
  const HEADINGS = {
    summary: /^(summary|professional summary|profile|about( me)?|objective|overview)$/,
    experience: /^((work|professional|relevant)\s+)?(experience|employment( history)?|work history|career history)$/,
    skills: /^((technical|core|key)\s+)?(skills|competencies|technologies|tech stack|tools)( (&|and) (tools|technologies))?$/,
    projects: /^((selected|personal|academic)\s+)?projects$/,
    education: /^(education|academic background|qualifications)$/,
    certifications: /^(certifications?|licenses?( (&|and) certifications)?|courses|training)$/,
    publications: /^(publications|research)$/,
    other: /^(interests|hobbies|references|languages|volunteer(ing)?|awards|achievements|activities|contact( info(rmation)?)?)$/,
  };
  const KEEP = ["summary", "experience", "skills", "projects", "certifications", "publications", "education"];

  function headingOf(line) {
    const t = line.trim().replace(/[:|•\-–—_*#]+$/g, "").replace(/^[#*•\-\s]+/, "").trim().toLowerCase();
    if (!t || t.length > 40) return null;
    for (const [key, re] of Object.entries(HEADINGS)) if (re.test(t)) return key;
    return null;
  }
  function splitSections(text) {
    const sections = { header: [] };
    let cur = "header";
    for (const line of text.split(/\r?\n/)) {
      const h = headingOf(line);
      if (h) { cur = h; sections[cur] ||= []; continue; }
      const inline = line.match(/^\s*([A-Za-z][A-Za-z &]{2,38}):\s*(\S.*)$/);   // "Skills: Python, SQL"
      const ih = inline && headingOf(inline[1]);
      if (ih) { cur = ih; (sections[cur] ||= []).push(inline[2]); continue; }
      (sections[cur] ||= []).push(line);
    }
    return Object.fromEntries(Object.entries(sections).map(([k, v]) => [k, v.join("\n").trim()]).filter(([, v]) => v));
  }

  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11 };
  const MON = "(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\\.?";
  const POINT = `(?:${MON}\\s+(\\d{4})|(\\d{1,2})\\/(\\d{4})|(\\d{4}))`;
  const RANGE = new RegExp(`${POINT}\\s*(?:-|–|—|to|until)\\s*(?:(present|current|now|today)|${POINT})`, "gi");

  function monthIndex(m) { return MONTHS[m.toLowerCase().replace(/\.$/, "").slice(0, m.toLowerCase().startsWith("sept") ? 4 : 3)]; }
  function point(g, now) {                    // g = [mon, yearAfterMon, mm, yearAfterMm, yearAlone]
    if (g[1]) return Number(g[1]) * 12 + monthIndex(g[0]);
    if (g[3]) return Number(g[3]) * 12 + Number(g[2]) - 1;
    if (g[4]) return Number(g[4]) * 12;
    return null;
  }
  function dateRanges(text, now = new Date()) {
    const nowM = now.getFullYear() * 12 + now.getMonth(), out = [];
    for (const m of text.matchAll(RANGE)) {
      const start = point(m.slice(1, 6), now);
      const end = m[6] ? nowM : point(m.slice(7, 12), now);
      if (start == null || end == null || end < start || start < 1950 * 12 || end > nowM + 1) continue;
      out.push([start, end + (m[11] && !m[7] && !m[9] ? 11 : 0)]);      // a bare end year runs to December
    }
    return out;
  }
  function experience(text, now) {
    const ranges = dateRanges(text, now).sort((a, b) => a[0] - b[0]);
    if (!ranges.length) return { roles: 0, years: null, span: null };
    let months = 0, [s, e] = ranges[0];                // merge overlaps so parallel roles are not double-counted
    for (const [a, b] of ranges.slice(1)) { if (a <= e) e = Math.max(e, b); else { months += e - s; [s, e] = [a, b]; } }
    months += e - s;
    const first = Math.floor(ranges[0][0] / 12), last = Math.floor(Math.max(...ranges.map(r => r[1])) / 12);
    return { roles: ranges.length, years: Math.round(months / 12 * 2) / 2, span: [first, last] };
  }

  const EMAIL = /[\w.+-]+@[\w-]+(\.[\w-]+)+/g;
  const URL_RE = /\b(?:https?:\/\/|www\.)\S+|\b(?:linkedin\.com|github\.com|gitlab\.com)\/\S*/gi;
  const PHONE = /(?<![\w])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]?\d{3,4}(?![\w])/g;
  function redact(text) {
    const removed = { emails: 0, phones: 0, links: 0 };
    const count = (re, key) => s => s.replace(re, () => { removed[key]++; return ""; });
    let t = count(EMAIL, "emails")(text);
    t = count(URL_RE, "links")(t);
    t = count(PHONE, "phones")(t);
    return { text: t.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n"), removed };
  }

  function prepare(raw, { now } = {}) {
    const sections = splitSections(raw);
    const found = Object.keys(sections).filter(k => k !== "header");
    const exp = experience(sections.experience || (found.length ? "" : raw), now);
    const kept = found.length ? KEEP.filter(k => sections[k]).map(k => `${k.toUpperCase()}\n${sections[k]}`).join("\n\n") : raw;
    const { text } = redact(kept);
    const { removed } = redact(raw);          // counted over the whole file: the header is dropped too
    const note = exp.roles ? `[Read in the browser: ${exp.roles} dated role(s), about ${exp.years} years of experience, ${exp.span[0]}-${exp.span[1]}]\n` : "";
    return { text: (note + text).slice(0, MAX_CHARS), sections: found, ...exp, removed,
             truncated: (note + text).length > MAX_CHARS };
  }

  window.NNResume = { readFile, prepare, splitSections, experience, redact, MAX_CHARS };
})();
