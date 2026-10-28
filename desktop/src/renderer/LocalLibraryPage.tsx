import { useEffect, useState, type FormEvent } from "react";
import type { LocalBook, ReadingApi } from "../shared/contracts";
import type { UiLanguage } from "./i18n";

type Api = NonNullable<ReadingApi["localLibrary"]>;

export function LocalLibraryPage({ language, api }: { language: UiLanguage; api: Api }) {
  const en = language === "en";
  const [books, setBooks] = useState<LocalBook[]>([]);
  const [selected, setSelected] = useState<LocalBook | null>(null);
  const [baseline, setBaseline] = useState<LocalBook | null>(null);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [error, setError] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    void api.list().then((items) => { if (active) { setBooks(items); setHasMore(items.length === 100); setLoadFailed(false); } }).catch(() => { if (active) { setError(true); setLoadFailed(true); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);
  useEffect(() => {
    if (!selected || !baseline || JSON.stringify(selected) === JSON.stringify(baseline)) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [baseline, selected]);
  async function add(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(false);
    try {
      const book = await api.add(title, author);
      setBooks((items) => [book, ...items]); setTitle(""); setAuthor("");
    } catch { setError(true); } finally { setBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true); setError(false);
    try {
      const book = await api.save(selected);
      setBooks((items) => items.map((item) => item.book_id === book.book_id ? { ...book, notes: "" } : item));
      setSelected(book); setBaseline(book); setSaved(true);
    } catch { setError(true); } finally { setBusy(false); }
  }
  return <section className="settings-panel local-library" aria-labelledby="local-library-title">
    <h1 id="local-library-title">{en ? "Local reading library" : "Biblioteca de leitura local"}</h1>
    <p>{en ? "Your reading records stay on this computer and do not require an account." : "Seus registros de leitura ficam neste computador e não exigem conta."}</p>
    {error ? <p role="alert">{en ? "Could not complete the operation. Check the values and try again." : "Não foi possível concluir. Confira os dados e tente novamente."}</p> : null}
    <form onSubmit={(event) => void add(event)}>
      <fieldset disabled={busy || loading || loadFailed}>
      <label>{en ? "Title" : "Título"}<input value={title} maxLength={200} required onChange={(event) => setTitle(event.target.value)} /></label>
      <label>{en ? "Author" : "Autor"}<input value={author} maxLength={200} onChange={(event) => setAuthor(event.target.value)} /></label>
      <button disabled={busy} type="submit">{en ? "Add book" : "Adicionar livro"}</button>
      </fieldset>
    </form>
    {loadFailed ? <button type="button" disabled={loading} onClick={() => {
      setLoading(true);
      void api.list().then((items) => { setBooks(items); setHasMore(items.length === 100); setLoadFailed(false); setError(false); }).catch(() => setError(true)).finally(() => setLoading(false));
    }}>{en ? "Retry loading" : "Tentar carregar novamente"}</button> : null}
    {loading ? <p>{en ? "Loading…" : "Carregando…"}</p> : loadFailed ? null : books.length === 0 ? <p>{en ? "No local books yet." : "Nenhum livro local ainda."}</p> : <ul>{books.map((book) => <li key={book.book_id}><button disabled={busy} type="button" onClick={() => {
      if (selected && baseline && JSON.stringify(selected) !== JSON.stringify(baseline) &&
        !window.confirm(en ? "Discard unsaved changes?" : "Descartar alterações não salvas?")) return;
      setBusy(true); setError(false);
      void api.get(book.book_id).then((fullBook) => { setSelected(fullBook); setBaseline(fullBook); setSaved(false); }).catch(() => setError(true)).finally(() => setBusy(false));
    }}>{book.title}</button> <span>{book.author}</span></li>)}</ul>}
    {hasMore ? <button type="button" disabled={busy} onClick={() => {
      setBusy(true); setError(false);
      void api.list(books.length).then((items) => { setBooks((current) => [...current, ...items]); setHasMore(items.length === 100); }).catch(() => setError(true)).finally(() => setBusy(false));
    }}>{en ? "Load more" : "Carregar mais"}</button> : null}
    {selected ? <form onChangeCapture={() => setSaved(false)} onSubmit={(event) => void save(event)}>
      <fieldset disabled={busy}>
      <h2>{selected.title}</h2>
      <label>{en ? "Status" : "Situação"}<select value={selected.status} onChange={(event) => setSelected({ ...selected, status: event.target.value as LocalBook["status"] })}>
        <option value="to_read">{en ? "To read" : "Quero ler"}</option><option value="reading">{en ? "Reading" : "Lendo"}</option><option value="finished">{en ? "Finished" : "Concluído"}</option><option value="stopped">{en ? "Stopped" : "Interrompido"}</option>
      </select></label>
      <label>{en ? "Progress (%)" : "Progresso (%)"}<input type="number" min="0" max="100" step="0.1" value={selected.progress ?? ""} onChange={(event) => setSelected({ ...selected, progress: event.target.value === "" ? null : Number(event.target.value) })} /></label>
      <label>{en ? "Rating (0–5)" : "Avaliação (0–5)"}<input type="number" min="0" max="5" step="0.5" value={selected.rating ?? ""} onChange={(event) => setSelected({ ...selected, rating: event.target.value === "" ? null : Number(event.target.value) })} /></label>
      <label>{en ? "Started" : "Início"}<input type="date" value={selected.started_on ?? ""} onChange={(event) => setSelected({ ...selected, started_on: event.target.value || null })} /></label>
      <label>{en ? "Finished" : "Fim"}<input type="date" value={selected.finished_on ?? ""} onChange={(event) => setSelected({ ...selected, finished_on: event.target.value || null })} /></label>
      <label>{en ? "Notes" : "Anotações"}<textarea maxLength={100000} value={selected.notes} onChange={(event) => setSelected({ ...selected, notes: event.target.value })} /></label>
      <button disabled={busy} type="submit">{en ? "Save reading" : "Salvar leitura"}</button>
      {saved ? <p role="status">{en ? "Reading saved locally." : "Leitura salva neste computador."}</p> : null}
      </fieldset>
    </form> : null}
  </section>;
}
