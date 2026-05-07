import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import {
  FileText,
  Scan,
  ShieldCheck,
  Upload,
  AlertTriangle,
  Lock,
  Send,
  Loader2,
  Inbox,
} from "lucide-react";
import { PIIPill, renderWithRedactions } from "@/components/PII";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const SUGGESTED = [
  "What is the total amount spent on dining?",
  "List the three largest transactions and their dates.",
  "How much was deposited during this period?",
  "Are there any recurring subscription charges?",
];

const INR_FMT = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function fmt(amount) {
  if (amount === null || amount === undefined || amount === "") return "—";
  const n = Number(amount);
  if (Number.isNaN(n)) return amount;
  return INR_FMT.format(n);
}

function Header({ keyOk }) {
  return (
    <header
      className="border-b border-zinc-200 bg-white"
      data-testid="app-header"
    >
      <div className="max-w-[1600px] mx-auto px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-zinc-900 text-white grid place-items-center rounded-sm">
            <ShieldCheck size={18} strokeWidth={1.5} />
          </div>
          <div>
            <div className="font-display text-lg leading-none">
              Ledger Sentinel
            </div>
            <div className="text-[11px] text-zinc-500 font-mono-num tracking-wider uppercase mt-1">
              langgraph · pii-shield · faiss
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="safety-badge" data-testid="badge-pii">
            <Lock size={12} strokeWidth={1.5} />
            PII MASKING · ON
          </span>
          <span className="safety-badge" data-testid="badge-safety">
            <ShieldCheck size={12} strokeWidth={1.5} />
            CONTENT GUARDRAIL · ON
          </span>
          <span
            className={`safety-badge ${
              keyOk ? "" : "border-amber-300 bg-amber-50 text-amber-700"
            }`}
            data-testid="badge-llm"
          >
            <Scan size={12} strokeWidth={1.5} />
            {keyOk ? "LLM KEY · OK" : "LLM KEY · MISSING"}
          </span>
        </div>
      </div>
    </header>
  );
}

function UploadCard({ onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [stage, setStage] = useState("");

  async function uploadFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setErr("Only PDF files are accepted.");
      return;
    }
    setErr(null);
    setBusy(true);
    setStage("Parsing PDF · masking PII · embedding to FAISS...");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await axios.post(`${API}/finance/upload`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 240000,
      });
      onUploaded(r.data);
    } catch (e) {
      setErr(
        e.response?.data?.detail || e.message || "Upload failed unexpectedly."
      );
    } finally {
      setBusy(false);
      setStage("");
    }
  }

  function onDrop(e) {
    e.preventDefault();
    if (busy) return;
    const f = e.dataTransfer.files?.[0];
    if (f) uploadFile(f);
  }

  return (
    <div className="bg-white border border-zinc-200 rounded-sm">
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center gap-2">
        <Upload size={14} strokeWidth={1.5} className="text-zinc-500" />
        <span className="text-xs uppercase tracking-wider text-zinc-500 font-mono-num">
          Ingest Statement
        </span>
      </div>
      <div className="p-4">
        <div
          onClick={() => !busy && inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
          data-testid="upload-pdf-dropzone"
          className={`relative overflow-hidden cursor-pointer border-2 border-dashed transition-colors duration-150 rounded-sm p-6 flex flex-col items-center justify-center text-center ${
            busy
              ? "scanning border-zinc-400 bg-zinc-50"
              : "border-zinc-300 hover:border-zinc-500 bg-white"
          }`}
        >
          {busy ? (
            <>
              <Loader2
                className="animate-spin text-zinc-700 mb-2"
                size={22}
                strokeWidth={1.5}
              />
              <div className="text-sm font-medium">Working...</div>
              <div className="text-[11px] text-zinc-500 font-mono-num mt-1">
                {stage}
              </div>
            </>
          ) : (
            <>
              <FileText size={26} strokeWidth={1.2} className="text-zinc-400" />
              <div className="text-sm font-medium mt-2">
                Drop a PDF here, or click
              </div>
              <div className="text-[11px] text-zinc-500 mt-1 font-mono-num tracking-wider">
                PDF · MAX 25MB · PII MASKED LOCALLY BEFORE EMBEDDING
              </div>
            </>
          )}
        </div>
        <input
          ref={inputRef}
          data-testid="upload-pdf-input"
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => uploadFile(e.target.files?.[0])}
        />
        {err && (
          <div
            className="mt-3 flex items-start gap-2 text-xs text-red-700 bg-red-50 border border-red-200 px-3 py-2 rounded-sm"
            data-testid="upload-error"
          >
            <AlertTriangle size={14} strokeWidth={1.5} className="mt-0.5" />
            <span>{err}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function DocList({ docs, activeId, onPick }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-sm">
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center gap-2">
        <Inbox size={14} strokeWidth={1.5} className="text-zinc-500" />
        <span className="text-xs uppercase tracking-wider text-zinc-500 font-mono-num">
          Documents · {docs.length}
        </span>
      </div>
      <div
        className="max-h-[44vh] overflow-y-auto scrollbar-thin"
        data-testid="documents-list"
      >
        {docs.length === 0 ? (
          <div className="px-4 py-8 text-xs text-zinc-500 text-center">
            No statements ingested yet.
          </div>
        ) : (
          docs.map((d) => {
            const active = d.doc_id === activeId;
            return (
              <button
                key={d.doc_id}
                data-testid={`document-item-${d.doc_id}`}
                onClick={() => onPick(d.doc_id)}
                className={`w-full text-left px-4 py-3 border-b border-zinc-100 transition-colors duration-150 ${
                  active ? "bg-zinc-100" : "bg-white hover:bg-zinc-50"
                }`}
              >
                <div className="text-sm font-medium truncate">
                  {d.original_name}
                </div>
                <div className="flex items-center gap-3 mt-1 text-[11px] font-mono-num text-zinc-500">
                  <span>{d.transaction_count ?? 0} tx</span>
                  <span>·</span>
                  <span>{d.pii_hits} pii</span>
                  <span>·</span>
                  <span>
                    {new Date(d.uploaded_at).toLocaleDateString()}{" "}
                    {new Date(d.uploaded_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

function SummaryCards({ summary }) {
  if (!summary) return null;
  const tiles = [
    { label: "Income", value: summary.income, tone: "text-emerald-700" },
    { label: "Expense", value: summary.expense, tone: "text-zinc-950" },
    { label: "Net", value: summary.net, tone: "text-zinc-950" },
    {
      label: "Top Category",
      value: summary.top_category || "—",
      tone: "text-zinc-950",
      mono: false,
    },
  ];
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {tiles.map((t) => (
        <div
          key={t.label}
          className="bg-white border border-zinc-200 rounded-sm px-4 py-3"
          data-testid={`summary-${t.label.toLowerCase().replace(/\s/g, "-")}`}
        >
          <div className="text-[10px] uppercase tracking-widest text-zinc-500 font-mono-num">
            {t.label}
          </div>
          <div
            className={`mt-2 text-2xl ${t.tone} ${
              t.mono === false ? "font-display" : "font-mono-num"
            }`}
          >
            {t.mono === false ? t.value : fmt(t.value)}
          </div>
        </div>
      ))}
    </div>
  );
}

function CategoryColors(cat) {
  const map = {
    Income: "bg-emerald-50 text-emerald-700 border-emerald-200",
    Dining: "bg-amber-50 text-amber-700 border-amber-200",
    Groceries: "bg-lime-50 text-lime-700 border-lime-200",
    Transport: "bg-sky-50 text-sky-700 border-sky-200",
    Fuel: "bg-orange-50 text-orange-700 border-orange-200",
    Utilities: "bg-indigo-50 text-indigo-700 border-indigo-200",
    "Rent/Mortgage": "bg-rose-50 text-rose-700 border-rose-200",
    Entertainment: "bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200",
    Shopping: "bg-pink-50 text-pink-700 border-pink-200",
    Subscriptions: "bg-violet-50 text-violet-700 border-violet-200",
    Travel: "bg-cyan-50 text-cyan-700 border-cyan-200",
    Transfers: "bg-zinc-100 text-zinc-700 border-zinc-300",
    "Fees/Interest": "bg-red-50 text-red-700 border-red-200",
    Healthcare: "bg-teal-50 text-teal-700 border-teal-200",
    Insurance: "bg-blue-50 text-blue-700 border-blue-200",
    Cash: "bg-yellow-50 text-yellow-700 border-yellow-200",
    Other: "bg-zinc-50 text-zinc-600 border-zinc-200",
  };
  return map[cat] || map.Other;
}

function TransactionsTable({ transactions }) {
  if (!transactions || transactions.length === 0) {
    return (
      <div className="bg-white border border-zinc-200 rounded-sm p-10 bg-grid">
        <div className="text-center text-sm text-zinc-500">
          No transactions extracted. Pick a document or upload a statement.
        </div>
      </div>
    );
  }
  return (
    <div
      className="bg-white border border-zinc-200 rounded-sm"
      data-testid="transactions-card"
    >
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-zinc-500 font-mono-num">
          Transactions · {transactions.length}
        </span>
        <span className="text-[11px] text-zinc-400 font-mono-num">
          values shown post-PII-mask
        </span>
      </div>
      <div className="overflow-x-auto max-h-[58vh] overflow-y-auto scrollbar-thin">
        <table
          className="w-full text-sm border-collapse"
          data-testid="transactions-table"
        >
          <thead className="sticky top-0 bg-white">
            <tr className="text-xs text-zinc-500 uppercase tracking-wider">
              <th className="text-left font-medium border-b border-zinc-200 px-4 py-2">
                Date
              </th>
              <th className="text-left font-medium border-b border-zinc-200 px-4 py-2">
                Description
              </th>
              <th className="text-left font-medium border-b border-zinc-200 px-4 py-2">
                Category
              </th>
              <th className="text-right font-medium border-b border-zinc-200 px-4 py-2">
                Amount
              </th>
              <th className="text-right font-medium border-b border-zinc-200 px-4 py-2">
                Balance
              </th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((t, i) => {
              const amt = Number(t.amount);
              const negative = amt < 0;
              return (
                <tr
                  key={i}
                  className="hover:bg-zinc-50"
                  data-testid={`tx-row-${i}`}
                >
                  <td className="border-b border-zinc-100 px-4 py-2 font-mono-num text-zinc-700">
                    {t.date || "—"}
                  </td>
                  <td className="border-b border-zinc-100 px-4 py-2">
                    {renderWithRedactions(t.description)}
                  </td>
                  <td className="border-b border-zinc-100 px-4 py-2">
                    <span
                      className={`inline-block px-2 py-0.5 text-[11px] border rounded-sm font-mono-num ${CategoryColors(
                        t.category
                      )}`}
                    >
                      {t.category || "Other"}
                    </span>
                  </td>
                  <td
                    className={`border-b border-zinc-100 px-4 py-2 text-right font-mono-num ${
                      negative ? "text-zinc-950" : "text-emerald-700"
                    }`}
                  >
                    {fmt(t.amount)}
                  </td>
                  <td className="border-b border-zinc-100 px-4 py-2 text-right font-mono-num text-zinc-500">
                    {t.balance ? fmt(t.balance) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ChatPanel({ activeId }) {
  const [msgs, setMsgs] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [msgs, busy]);

  async function ask(question) {
    if (!activeId || !question.trim() || busy) return;
    const userMsg = { role: "user", text: question };
    setMsgs((m) => [...m, userMsg]);
    setBusy(true);
    setQ("");
    try {
      const r = await axios.post(`${API}/finance/query`, {
        doc_id: activeId,
        question,
      });
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: r.data.answer,
          sources: r.data.sources,
          blocked: r.data.safety_blocked,
        },
      ]);
    } catch (e) {
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: e.response?.data?.detail || e.message || "Request failed.",
          error: true,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="bg-white border border-zinc-200 rounded-sm flex flex-col h-[78vh]"
      data-testid="chat-panel"
    >
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-zinc-500 font-mono-num">
          Q&A · grounded · pii-safe
        </span>
        <span className="safety-badge">
          <ShieldCheck size={12} strokeWidth={1.5} />
          GUARDED
        </span>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-3 scrollbar-thin"
        data-testid="chat-history"
      >
        {!activeId && (
          <div className="text-xs text-zinc-500">
            Select or upload a statement to ask questions.
          </div>
        )}
        {activeId && msgs.length === 0 && (
          <div>
            <div className="text-xs text-zinc-500 mb-2">Try:</div>
            <div className="flex flex-col gap-2">
              {SUGGESTED.map((s) => (
                <button
                  key={s}
                  onClick={() => ask(s)}
                  data-testid="suggested-question"
                  className="text-left text-xs px-3 py-2 border border-zinc-200 hover:border-zinc-400 hover:bg-zinc-50 rounded-sm transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div
              key={i}
              className="bg-zinc-100 text-zinc-950 rounded-sm p-3 text-sm"
              data-testid="chat-msg-user"
            >
              {m.text}
            </div>
          ) : (
            <div
              key={i}
              className={`border rounded-sm p-3 text-sm ${
                m.blocked || m.error
                  ? "border-red-200 bg-red-50 text-red-800"
                  : "border-zinc-200 bg-white text-zinc-800"
              }`}
              data-testid="chat-msg-ai"
            >
              <div className="flex items-center gap-2 mb-1">
                <ShieldCheck
                  size={12}
                  strokeWidth={1.5}
                  className={m.blocked ? "text-red-600" : "text-emerald-700"}
                />
                <span className="text-[10px] font-mono-num uppercase tracking-wider text-zinc-500">
                  {m.blocked ? "blocked by safety" : "grounded answer"}
                </span>
              </div>
              <div className="whitespace-pre-wrap leading-relaxed">
                {renderWithRedactions(m.text)}
              </div>
              {m.sources?.length > 0 && (
                <div className="mt-2 pt-2 border-t border-zinc-200">
                  <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono-num mb-1">
                    Sources
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {m.sources.map((s, j) => (
                      <span
                        key={j}
                        title={s.snippet}
                        className="text-[10px] px-1.5 py-0.5 border border-zinc-200 bg-zinc-50 rounded-sm font-mono-num"
                      >
                        chunk {s.chunk}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )
        )}
        {busy && (
          <div className="border border-zinc-200 bg-white rounded-sm p-3 text-xs text-zinc-500 flex items-center gap-2">
            <Loader2 size={14} className="animate-spin" /> Thinking...
          </div>
        )}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
        className="border-t border-zinc-200 px-3 py-3 flex items-center gap-2"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={activeId ? "Ask a question..." : "Pick a document first"}
          disabled={!activeId || busy}
          data-testid="chat-input"
          className="flex-1 bg-transparent outline-none border-b border-zinc-300 focus:border-zinc-900 transition-colors py-2 text-sm font-mono-num placeholder:text-zinc-400 disabled:opacity-40"
        />
        <button
          type="submit"
          disabled={!activeId || busy || !q.trim()}
          data-testid="chat-submit-button"
          className="bg-zinc-900 hover:bg-zinc-800 disabled:bg-zinc-300 text-white rounded-sm px-3 py-2 text-sm flex items-center gap-1 transition-colors"
        >
          <Send size={14} strokeWidth={1.5} /> Ask
        </button>
      </form>
    </div>
  );
}

export default function Dashboard() {
  const [docs, setDocs] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [activeDoc, setActiveDoc] = useState(null);
  const [keyOk, setKeyOk] = useState(true);

  async function refreshDocs(selectId) {
    const r = await axios.get(`${API}/finance/documents`);
    setDocs(r.data);
    if (selectId) setActiveId(selectId);
    else if (!activeId && r.data.length > 0) setActiveId(r.data[0].doc_id);
  }

  async function loadActive() {
    if (!activeId) {
      setActiveDoc(null);
      return;
    }
    const r = await axios.get(`${API}/finance/documents/${activeId}`);
    setActiveDoc(r.data);
  }

  useEffect(() => {
    axios
      .get(`${API}/finance/health`)
      .then((r) => setKeyOk(!!r.data.has_emergent_key))
      .catch(() => setKeyOk(false));
    refreshDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadActive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  const transactions = useMemo(
    () => activeDoc?.transactions || [],
    [activeDoc]
  );

  return (
    <div className="min-h-screen">
      <Header keyOk={keyOk} />
      <main className="max-w-[1600px] mx-auto p-4 md:p-6 lg:p-8">
        <div className="mb-6 flex items-end justify-between flex-wrap gap-3">
          <div>
            <h1 className="font-display text-3xl tracking-tight">
              Statement Analyzer
            </h1>
            <p className="text-sm text-zinc-500 mt-1">
              Multi-agent extraction · LangGraph · FAISS · privacy-first
            </p>
          </div>
          <div className="text-[11px] font-mono-num text-zinc-500 uppercase tracking-wider">
            gemini-3.1-pro · claude-sonnet-4.5
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 lg:gap-6">
          {/* Left rail */}
          <aside className="lg:col-span-3 space-y-4">
            <UploadCard onUploaded={(meta) => refreshDocs(meta.doc_id)} />
            <DocList
              docs={docs}
              activeId={activeId}
              onPick={setActiveId}
            />
          </aside>

          {/* Center */}
          <section className="lg:col-span-6 space-y-4">
            <SummaryCards summary={activeDoc?.summary} />
            {activeDoc?.graph_error && (
              <div className="border border-amber-200 bg-amber-50 text-amber-800 text-xs px-3 py-2 rounded-sm flex items-start gap-2">
                <AlertTriangle size={14} className="mt-0.5" />
                <div>
                  Extraction graph reported: {activeDoc.graph_error}
                </div>
              </div>
            )}
            <TransactionsTable transactions={transactions} />
          </section>

          {/* Right rail */}
          <aside className="lg:col-span-3">
            <ChatPanel activeId={activeId} />
          </aside>
        </div>

        <footer className="mt-8 text-[11px] text-zinc-400 font-mono-num flex items-center gap-3 justify-between">
          <span>
            <PIIPill last4="SAFE" /> values are masked before reaching the
            model.
          </span>
          <span>ledger-sentinel-langraph · v0.1</span>
        </footer>
      </main>
    </div>
  );
}
