from __future__ import annotations

import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, cast

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry, HardcoverRead
from koreader_ecosystem.ui.operation_worker import CancellationToken, OperationController


class ReadingPage(QWidget):
    _PAGE_SIZE = 100
    _MAX_COVER_BYTES = 2_000_000
    _MAX_CONCURRENT_COVERS = 3
    _MAX_CACHED_COVERS = 48
    _SHELVES = ("reading", "want", "paused", "completed", "dnf")
    _SHELF_TITLES = {
        "reading": "Lendo agora",
        "want": "Quero ler",
        "paused": "Pausados",
        "completed": "Concluídos",
        "dnf": "Não terminei",
    }

    def __init__(self, services: Any, operations: OperationController) -> None:
        super().__init__()
        self.setObjectName("readingsPage")
        self.services = services
        self.operations = operations
        self._entries: tuple[HardcoverLibraryEntry, ...] = ()
        self._shown_entries: tuple[HardcoverLibraryEntry, ...] = ()
        self._pending_action: str | None = None
        self._refresh_after_mutation = False
        self._empty_messages = {
            "reading": "Nenhum livro em andamento. Use o filtro para ver sua estante.",
            "want": "Sua lista de próximas leituras está vazia.",
            "paused": "Nenhum livro está pausado.",
            "completed": "Seus livros concluídos aparecerão aqui.",
            "dnf": "Nenhum livro marcado como não terminado.",
        }
        self._book_layout: QVBoxLayout
        self._empty_label: QLabel
        self._library_scroll: QScrollArea
        self._cover_targets: dict[str, list[tuple[QFrame, QLabel]]] = {
            key: [] for key in self._SHELVES
        }
        self._cover_cache: dict[str, QPixmap] = {}
        self._cover_queue: list[tuple[QLabel, str]] = []
        self._cover_replies: dict[str, QNetworkReply] = {}
        self._cover_generation = 0
        self._cover_network = QNetworkAccessManager(self)
        self._cover_network.setTransferTimeout(8_000)

        page_layout = QHBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addStretch(1)
        content_column = QWidget()
        content_column.setObjectName("readingContent")
        content_column.setMinimumWidth(0)
        content_column.setMaximumWidth(960)
        root = QVBoxLayout(content_column)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        page_layout.addWidget(content_column, 100)
        page_layout.addStretch(1)

        header = QWidget()
        header.setObjectName("readingsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(16)

        heading_block = QWidget()
        heading_layout = QVBoxLayout(heading_block)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(4)
        heading = QLabel("Estante")
        heading.setObjectName("pageTitle")
        intro = QLabel("Sua leitura e sua biblioteca do Hardcover, em um só lugar.")
        intro.setObjectName("pageDescription")
        intro.setWordWrap(True)
        heading_layout.addWidget(heading)
        heading_layout.addWidget(intro)
        header_layout.addWidget(heading_block, 1)

        self.refresh_button = QPushButton("Atualizar estante")
        self.refresh_button.setObjectName("refreshReadings")
        self.refresh_button.setProperty("primary", True)
        header_layout.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        self.status_label = QLabel("Ainda não carregamos suas leituras.")
        self.status_label.setObjectName("readingsStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.sync_confirmation_label = QLabel()
        self.sync_confirmation_label.setObjectName("hardcoverSyncConfirmation")
        self.sync_confirmation_label.setWordWrap(True)
        root.addWidget(self.sync_confirmation_label)
        self._update_sync_confirmation()

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("readingSearchEdit")
        self.search_edit.setAccessibleName("Buscar livro por título ou autor")
        self.search_edit.setPlaceholderText("Buscar por título ou autor…")
        self.search_edit.setClearButtonEnabled(True)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(self.search_edit, 1)
        self.shelf_filter = QComboBox()
        self.shelf_filter.setObjectName("readingShelfFilter")
        self.shelf_filter.setAccessibleName("Filtrar estante por status de leitura")
        for key in self._SHELVES:
            self.shelf_filter.addItem(self._SHELF_TITLES[key], key)
        self.shelf_filter.setCurrentIndex(0)
        self.shelf_filter.setMinimumWidth(165)
        filter_row.addWidget(self.shelf_filter)
        root.addLayout(filter_row)

        self.digital_filter = QCheckBox("Somente Kindle/eBook")
        self.digital_filter.setObjectName("digitalEditionFilter")
        self.digital_filter.setAccessibleName("Mostrar somente edições Kindle ou eBook")
        self.digital_filter.setToolTip(
            "Filtro apenas nesta tela. Desative para incluir formatos físicos e não identificados; "
            "sua biblioteca no Hardcover não será alterada."
        )
        self.digital_filter.setChecked(True)
        format_row = QHBoxLayout()
        format_row.addWidget(self.digital_filter)
        format_row.addStretch(1)
        root.addLayout(format_row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self._filter_visible_cards)

        self._library_scroll = QScrollArea()
        self._library_scroll.setObjectName("readingLibraryScroll")
        self._library_scroll.setWidgetResizable(True)
        self._library_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._library_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setMinimumWidth(0)
        self._book_layout = QVBoxLayout(content)
        self._book_layout.setContentsMargins(0, 8, 0, 0)
        self._book_layout.setSpacing(10)
        self._empty_label = QLabel(self._empty_messages["reading"])
        self._empty_label.setObjectName("empty_reading")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setMinimumHeight(128)
        self._empty_label.setWordWrap(True)
        self._book_layout.addWidget(self._empty_label)
        self._book_layout.addStretch(1)
        self._library_scroll.setWidget(content)
        self._library_scroll.verticalScrollBar().valueChanged.connect(
            lambda _value: self._load_visible_covers()
        )
        root.addWidget(self._library_scroll, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        self.shelf_filter.currentIndexChanged.connect(self._filter_visible_cards)
        self.digital_filter.stateChanged.connect(lambda _state: self._filter_visible_cards())
        self.operations.completed.connect(self._on_completed)
        self.operations.failed.connect(self._on_failed)
        self.operations.busyChanged.connect(self._on_busy_changed)
        self.operations.progress.connect(self._show_progress)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._load_visible_covers)

    def refresh(self) -> None:
        self._start_operation(
            "refresh",
            "Consultando toda a biblioteca do Hardcover…",
            self._load_all_books,
        )

    def _load_all_books(
        self, token: CancellationToken
    ) -> tuple[HardcoverLibraryEntry, ...] | object:
        entries: list[HardcoverLibraryEntry] = []
        offset = 0
        while True:
            token.raise_if_cancelled()
            token.report_progress(f"Carregando sua biblioteca… {len(entries)} livros encontrados.")
            page = self.services.list_hardcover_books(
                limit=self._PAGE_SIZE,
                offset=offset,
            )
            if not isinstance(page, tuple) or not all(
                isinstance(item, HardcoverLibraryEntry) for item in page
            ):
                return cast(object, page)
            entries.extend(page)
            if len(page) < self._PAGE_SIZE:
                return tuple(entries)
            offset += len(page)

    def set_entries(
        self,
        entries: tuple[HardcoverLibraryEntry, ...],
    ) -> None:
        self._entries = tuple(entries)
        self._filter_visible_cards()

    def _filter_visible_cards(self, _query: str | None = None) -> None:
        query = self._normalize_search(self.search_edit.text())
        selected_shelf = self.shelf_filter.currentData()
        if not isinstance(selected_shelf, str) or selected_shelf not in self._SHELVES:
            selected_shelf = "reading"
        digital_only = self.digital_filter.isChecked()
        category_counts = {key: 0 for key in self._SHELVES}
        category_totals = {key: 0 for key in self._SHELVES}
        visible_entries: list[HardcoverLibraryEntry] = []

        for entry in self._entries:
            shelf = self._shelf_for_status(entry.status_id)
            if shelf is None:
                continue
            category_totals[shelf] += 1
            if digital_only and not self._is_digital_format(entry.edition_format):
                continue
            category_counts[shelf] += 1
            if shelf != selected_shelf:
                continue
            search_text = self._normalize_search(f"{entry.title} {' '.join(entry.authors)}")
            if not query or query in search_text:
                visible_entries.append(entry)

        for index, key in enumerate(self._SHELVES):
            self.shelf_filter.setItemText(
                index,
                f"{self._SHELF_TITLES[key]} ({category_counts[key]})",
            )

        if query and category_counts[selected_shelf]:
            self._empty_label.setText("Nenhum livro corresponde a essa busca nesta estante.")
        elif (
            digital_only and category_totals[selected_shelf] and not category_counts[selected_shelf]
        ):
            hidden = category_totals[selected_shelf]
            self._empty_label.setText(
                f"{hidden} livro(s) nesta estante estão fora do filtro Kindle/eBook. "
                "Desative o filtro para ver todas as edições."
            )
        else:
            self._empty_label.setText(self._empty_messages[selected_shelf])

        self._shown_entries = tuple(visible_entries)
        self._empty_label.setObjectName(f"empty_{selected_shelf}")
        self._render_visible_cards(selected_shelf, self._shown_entries)
        self._empty_label.setVisible(not visible_entries)

        loaded = len(self._entries)
        if loaded == 0:
            status = "Sua estante do Hardcover está vazia."
        elif query:
            status = f"{len(visible_entries)} resultado(s) nesta estante."
        elif digital_only:
            hidden = category_totals[selected_shelf] - category_counts[selected_shelf]
            status = (
                f"{len(visible_entries)} livros Kindle/eBook nesta estante · "
                f"{hidden} fora do filtro."
            )
        else:
            status = f"{len(visible_entries)} livros nesta estante · {loaded} carregados."
        self.status_label.setText(status)
        QTimer.singleShot(0, self._load_visible_covers)

    def _render_visible_cards(
        self,
        shelf: str,
        entries: tuple[HardcoverLibraryEntry, ...],
    ) -> None:
        self._cover_generation += 1
        self._cancel_cover_requests()
        self._cover_targets = {key: [] for key in self._SHELVES}

        while self._book_layout.count():
            item = self._book_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None and widget is not self._empty_label:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        self._book_layout.addWidget(self._empty_label)
        for entry in entries:
            if shelf == "reading":
                card = self._reading_card(entry)
            elif shelf == "want":
                card = self._want_card(entry)
            elif shelf == "paused":
                card = self._paused_card(entry)
            elif shelf == "completed":
                card = self._rating_card(entry)
            else:
                card = self._dnf_card(entry)
            self._book_layout.addWidget(card)
        self._book_layout.addStretch(1)

    def _visible_entries(self) -> tuple[HardcoverLibraryEntry, ...]:
        return self._shown_entries

    @classmethod
    def _shelf_for_status(cls, status_id: int) -> str | None:
        return {
            1: "want",
            2: "reading",
            3: "completed",
            4: "paused",
            5: "dnf",
        }.get(status_id)

    @staticmethod
    def _is_digital_format(edition_format: str | None) -> bool:
        if not isinstance(edition_format, str) or not edition_format.strip():
            return False
        compact = "".join(char for char in edition_format.casefold() if char.isalnum())
        if any(marker in compact for marker in ("audio", "audible")):
            return False
        return any(
            marker in compact
            for marker in ("kindle", "ebook", "digital", "epub", "mobi", "azw", "pdf")
        )

    @staticmethod
    def _normalize_search(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.casefold())
        return "".join(
            character for character in normalized if not unicodedata.combining(character)
        )

    def _reading_card(self, entry: HardcoverLibraryEntry) -> QWidget:
        card, layout, _cover = self._base_card(entry, "reading", "LENDO AGORA")
        active_read = self._active_read(entry)
        if active_read is None:
            progress_label = QLabel("Leitura sem registro ativo no Hardcover.")
        else:
            progress_label = QLabel(self._progress_text(entry, active_read))
        progress_label.setObjectName(f"progressLabel_{entry.user_book_id}")
        progress_label.setProperty("textRole", "progressText")
        progress_label.setWordWrap(True)
        layout.addWidget(progress_label)

        if active_read is not None and entry.edition_pages:
            progress = QProgressBar()
            progress.setObjectName("bookProgress")
            progress.setRange(0, 100)
            progress.setValue(
                min(100, round(active_read.progress_pages / entry.edition_pages * 100))
            )
            progress.setTextVisible(False)
            progress.setAccessibleName(f"Progresso de {entry.title}: {progress.value()} por cento")
            layout.addWidget(progress)

        if active_read is not None:
            finish = QPushButton("Marcar como concluído")
            finish.setObjectName(f"finishRead_{entry.user_book_id}")
            finish.setProperty("primary", True)
            finish.setMaximumWidth(220)
            finish.clicked.connect(
                lambda _checked=False, read_id=active_read.id: self._finish_read(read_id)
            )
            layout.addWidget(finish, 0, Qt.AlignmentFlag.AlignRight)
        elif entry.edition_id is not None:
            start = QPushButton("Criar registro de leitura")
            start.setObjectName(f"startRead_{entry.user_book_id}")
            start.setMaximumWidth(220)
            start.clicked.connect(lambda _checked=False, item=entry: self._start_read(item))
            layout.addWidget(start, 0, Qt.AlignmentFlag.AlignRight)
        return card

    def _want_card(self, entry: HardcoverLibraryEntry) -> QWidget:
        card, layout, _cover = self._base_card(entry, "want", "QUERO LER")
        if entry.edition_id is None:
            note = QLabel("Vincule uma edição no Hardcover antes de iniciar.")
            note.setObjectName(f"editionRequired_{entry.user_book_id}")
            note.setWordWrap(True)
            layout.addWidget(note)
            return card
        start = QPushButton("Começar a ler")
        start.setObjectName(f"startRead_{entry.user_book_id}")
        start.setProperty("primary", True)
        start.setMaximumWidth(220)
        start.clicked.connect(lambda _checked=False, item=entry: self._start_read(item))
        layout.addWidget(start, 0, Qt.AlignmentFlag.AlignRight)
        return card

    def _rating_card(self, entry: HardcoverLibraryEntry) -> QWidget:
        card, layout, _cover = self._base_card(entry, "completed", "CONCLUÍDO")
        latest = entry.reads[-1] if entry.reads else None
        completed = latest.finished_at if latest else None
        finish_label = QLabel(
            f"Concluído em {self._format_date(completed)}"
            if completed
            else "Concluído no Hardcover; data de término indisponível."
        )
        finish_label.setObjectName(f"finishedDate_{entry.user_book_id}")
        layout.addWidget(finish_label)

        rating_row = QHBoxLayout()
        rating_label = QLabel("Sua avaliação")
        rating = self._rating_selector(entry)
        rating_row.addWidget(rating_label)
        rating_row.addWidget(rating)
        rating_row.addStretch(1)
        layout.addLayout(rating_row)
        save_rating = QPushButton("Salvar avaliação")
        save_rating.setObjectName(f"saveRating_{entry.user_book_id}")
        save_rating.setProperty("primary", True)
        save_rating.setEnabled(entry.rating is not None)
        rating.currentIndexChanged.connect(
            lambda _index, selector=rating, button=save_rating: button.setEnabled(
                selector.currentData() is not None
            )
        )
        save_rating.clicked.connect(
            lambda _checked=False, item=entry, control=rating: self._save_rating(
                item.user_book_id, control.currentData()
            )
        )
        layout.addWidget(save_rating)

        note = QPlainTextEdit()
        note.setObjectName(f"journalEntry_{entry.user_book_id}")
        note.setPlaceholderText("Anotação opcional no diário do Hardcover")
        note.setMaximumHeight(88)
        note.setMinimumHeight(78)
        layout.addWidget(note)

        privacy = QComboBox()
        privacy.setObjectName(f"journalPrivacy_{entry.user_book_id}")
        privacy.addItem("Privada", 3)
        privacy.addItem("Seguidores", 2)
        privacy.addItem("Pública", 1)
        layout.addWidget(privacy)

        save_note = QPushButton("Salvar anotação")
        save_note.setObjectName(f"saveJournal_{entry.user_book_id}")
        save_note.setEnabled(False)
        note.textChanged.connect(
            lambda editor=note, button=save_note: button.setEnabled(
                bool(editor.toPlainText().strip())
            )
        )
        save_note.clicked.connect(
            lambda _checked=False, item=entry, editor=note, control=privacy: self._save_note(
                item.book_id,
                editor.toPlainText(),
                int(control.currentData()),
            )
        )
        layout.addWidget(save_note)
        return card

    def _dnf_card(self, entry: HardcoverLibraryEntry) -> QWidget:
        card, layout, _cover = self._base_card(entry, "dnf", "NÃO TERMINEI")
        latest = entry.reads[-1] if entry.reads else None
        if latest and latest.started_at:
            layout.addWidget(QLabel(f"Iniciada em {self._format_date(latest.started_at)}"))
        return card

    def _paused_card(self, entry: HardcoverLibraryEntry) -> QWidget:
        card, layout, _cover = self._base_card(entry, "paused", "PAUSADO")
        latest = entry.reads[-1] if entry.reads else None
        if latest and latest.started_at:
            layout.addWidget(QLabel(f"Leitura iniciada em {self._format_date(latest.started_at)}"))
        return card

    @staticmethod
    def _rating_selector(entry: HardcoverLibraryEntry) -> QComboBox:
        rating = QComboBox()
        rating.setObjectName(f"ratingCombo_{entry.user_book_id}")
        rating.addItem("Escolha uma nota", None)
        for step in range(1, 11):
            value = step / 2
            number = f"{value:.1f}".replace(".", ",")
            rating.addItem(f"{number} / 5 estrelas", value)
        if entry.rating is not None:
            rating.setCurrentIndex(rating.findData(entry.rating))
        return rating

    def _base_card(
        self, entry: HardcoverLibraryEntry, shelf: str, badge_text: str
    ) -> tuple[QFrame, QVBoxLayout, QLabel]:
        card = QFrame()
        card.setObjectName(f"readingCard_{entry.user_book_id}")
        card.setProperty("surfaceRole", "bookCard")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        cover = QLabel()
        cover.setObjectName(f"bookCover_{entry.user_book_id}")
        cover.setFixedSize(88, 132)
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover.setWordWrap(True)
        cover.setAccessibleName(f"Capa de {entry.title}")
        cover.setToolTip(entry.title)
        initials = "".join(part[0] for part in entry.title.split()[:2]).upper()
        cover.setText(initials or "LIVRO")
        cover.setProperty("coverUrl", entry.cover_url or "")
        cover.setProperty("coverLoaded", False)
        tones = ("#475569", "#7f1d1d", "#312e81", "#701a75", "#1e40af", "#9a3412")
        tone = tones[sum(map(ord, entry.title)) % len(tones)]
        cover.setStyleSheet(
            f"background: {tone}; color: #f8fafc; border-radius: 8px; "
            "font-family: Inter; font-size: 19px; font-weight: 700; padding: 7px;"
        )
        outer.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)

        details = QWidget()
        layout = QVBoxLayout(details)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addWidget(details, 1)

        badge = QLabel(badge_text)
        badge.setObjectName("bookStatusBadge")
        badge.setProperty("textRole", "bookStatusBadge")
        badge.setProperty("shelfStatus", shelf)
        badge.setMaximumWidth(140)
        title = QLabel(entry.title)
        title.setObjectName(f"bookTitle_{entry.user_book_id}")
        title.setProperty("textRole", "bookTitle")
        title.setWordWrap(True)
        authors = ", ".join(entry.authors) or "Autor não informado"
        author = QLabel(authors)
        author.setObjectName(f"bookAuthors_{entry.user_book_id}")
        author.setProperty("textRole", "bookAuthor")
        author.setWordWrap(True)
        edition_format = entry.edition_format or "Formato não identificado"
        format_label = QLabel(edition_format)
        format_label.setObjectName(f"bookFormat_{entry.user_book_id}")
        format_label.setProperty("textRole", "bookFormat")
        format_label.setAccessibleName(f"Formato da edição: {edition_format}")
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title)
        layout.addWidget(author)
        layout.addWidget(format_label)
        self._cover_targets[shelf].append((card, cover))
        return card, layout, cover

    def _load_visible_covers(self) -> None:
        if not self.isVisible():
            return
        selected_shelf = self.shelf_filter.currentData()
        if not isinstance(selected_shelf, str) or selected_shelf not in self._SHELVES:
            return
        viewport = self._library_scroll.viewport()
        visible = viewport.rect().adjusted(0, -180, 0, 180)
        for card, cover in self._cover_targets[selected_shelf]:
            url = cover.property("coverUrl")
            if not isinstance(url, str) or not url or cover.property("coverLoaded"):
                continue
            if not card.isVisible():
                continue
            card_top_left = card.mapTo(viewport, QPoint(0, 0))
            card_bounds = QRect(card_top_left, card.size())
            if not visible.intersects(card_bounds):
                continue
            cached = self._cover_cache.get(url)
            if cached is not None:
                self._set_cover_image(cover, cached)
                continue
            if url not in self._cover_replies and not any(
                queued_url == url for _queued_cover, queued_url in self._cover_queue
            ):
                cover.setProperty("coverQueued", True)
                self._cover_queue.append((cover, url))
        self._start_cover_requests()

    def _start_cover_requests(self) -> None:
        while self._cover_queue and len(self._cover_replies) < self._MAX_CONCURRENT_COVERS:
            cover, url = self._cover_queue.pop(0)
            if not cover.property("coverQueued") or cover.property("coverLoaded"):
                continue
            parsed = QUrl(url)
            hostname = parsed.host().lower()
            if (
                not parsed.isValid()
                or parsed.scheme() != "https"
                or not (hostname == "hardcover.app" or hostname.endswith(".hardcover.app"))
            ):
                cover.setProperty("coverLoaded", True)
                cover.setProperty("coverQueued", False)
                continue
            if url in self._cover_replies:
                continue
            request = QNetworkRequest(parsed)
            request.setAttribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
            )
            reply = self._cover_network.get(request)
            reply.setReadBufferSize(self._MAX_COVER_BYTES + 1)
            generation = self._cover_generation
            reply.readyRead.connect(lambda current=reply: self._abort_large_cover(current))
            reply.finished.connect(
                lambda current=reply, source=url, current_generation=generation: self._finish_cover(
                    current, source, current_generation
                )
            )
            self._cover_replies[url] = reply

    def _abort_large_cover(self, reply: QNetworkReply) -> None:
        if reply.bytesAvailable() > self._MAX_COVER_BYTES:
            reply.abort()

    def _finish_cover(self, reply: QNetworkReply, url: str, generation: int) -> None:
        if self._cover_replies.get(url) is reply:
            self._cover_replies.pop(url, None)
        if generation != self._cover_generation:
            reply.deleteLater()
            self._start_cover_requests()
            return
        pixmap = QPixmap()
        if reply.error() == QNetworkReply.NetworkError.NoError:
            image_data = bytes(reply.readAll().data())
            if len(image_data) <= self._MAX_COVER_BYTES:
                pixmap.loadFromData(image_data)
        reply.deleteLater()
        if not pixmap.isNull():
            if len(self._cover_cache) >= self._MAX_CACHED_COVERS:
                self._cover_cache.pop(next(iter(self._cover_cache)))
            self._cover_cache[url] = pixmap
            for targets in self._cover_targets.values():
                for _card, cover in targets:
                    if cover.property("coverUrl") == url:
                        self._set_cover_image(cover, pixmap)
        else:
            for targets in self._cover_targets.values():
                for _card, cover in targets:
                    if cover.property("coverUrl") == url:
                        cover.setProperty("coverLoaded", True)
                        cover.setProperty("coverQueued", False)
        self._start_cover_requests()

    @staticmethod
    def _set_cover_image(cover: QLabel, image: QPixmap) -> None:
        scaled = image.scaled(
            cover.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = max(0, (scaled.width() - cover.width()) // 2)
        top = max(0, (scaled.height() - cover.height()) // 2)
        cover.setPixmap(scaled.copy(left, top, cover.width(), cover.height()))
        cover.setText("")
        cover.setStyleSheet("background: #111827; border-radius: 8px;")
        cover.setProperty("coverLoaded", True)
        cover.setProperty("coverQueued", False)

    def _cancel_cover_requests(self) -> None:
        for reply in self._cover_replies.values():
            try:
                reply.finished.disconnect()
            except RuntimeError, TypeError:
                pass
            reply.abort()
            reply.deleteLater()
        self._cover_replies.clear()
        self._cover_queue.clear()

    def _save_rating(self, user_book_id: int, rating: object) -> None:
        if not isinstance(rating, int | float) or isinstance(rating, bool):
            self.status_label.setText("Escolha uma nota antes de salvar.")
            return
        value = float(rating)
        if value < 0.5 or value > 5 or round(value * 2) != value * 2:
            self.status_label.setText("Escolha uma nota entre 0,5 e 5 estrelas.")
            return
        self._start_operation(
            "rating",
            "Salvando avaliação…",
            lambda token: self.services.update_rating(
                user_book_id=user_book_id,
                rating=value,
            ),
        )

    def _start_read(self, entry: HardcoverLibraryEntry) -> None:
        if entry.edition_id is None:
            self.status_label.setText("Vincule uma edição explícita antes de começar.")
            return
        self._start_operation(
            "start",
            "Iniciando leitura no Hardcover…",
            lambda token: self.services.start_read(
                user_book_id=entry.user_book_id,
                edition_id=entry.edition_id,
                page=0,
                started_at=date.today(),
            ),
        )

    def _finish_read(self, read_id: int) -> None:
        self._start_operation(
            "finish",
            "Registrando conclusão no Hardcover…",
            lambda token: self.services.finish_read(read_id=read_id, finished_at=date.today()),
        )

    def _save_note(self, book_id: int, entry: str, privacy_setting_id: int) -> None:
        if not entry.strip():
            self.status_label.setText("Escreva uma anotação antes de salvar.")
            return
        self._start_operation(
            "journal",
            "Salvando anotação no Hardcover…",
            lambda token: self.services.add_journal_entry(
                book_id=book_id,
                entry=entry,
                privacy_setting_id=privacy_setting_id,
            ),
        )

    def _start_operation(
        self,
        action: str,
        title: str,
        operation: Callable[[CancellationToken], object],
    ) -> None:
        self._pending_action = action
        status_key = "hardcover_library_refresh" if action == "refresh" else "hardcover_mutation"
        if not self.operations.start(title, operation, status_key=status_key):
            self._pending_action = None
            self.status_label.setText("Aguarde a tarefa em andamento terminar.")

    def _on_completed(self, result: object) -> None:
        action = self._pending_action
        if action is None:
            return
        self._pending_action = None
        if action == "refresh":
            if not isinstance(result, tuple) or not all(
                isinstance(item, HardcoverLibraryEntry) for item in result
            ):
                self._show_error("invalid_response")
                return
            self.set_entries(result)
            self.status_label.setText(
                "Sua lista está vazia."
                if not result
                else f"Biblioteca atualizada: {len(result)} livros carregados do Hardcover."
            )
            self._update_sync_confirmation()
        else:
            self.status_label.setText("Alteração confirmada pelo Hardcover.")
            self._refresh_after_mutation = True

    def _on_failed(self, reason_code: str) -> None:
        if self._pending_action is None:
            return
        self._pending_action = None
        self._show_error(reason_code)
        self._update_sync_confirmation()

    def _on_busy_changed(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)
        if not busy and self._refresh_after_mutation:
            self._refresh_after_mutation = False
            self.refresh()

    def _show_progress(self, title: str) -> None:
        if self._pending_action is not None:
            self.status_label.setText(title)

    def _show_error(self, reason_code: str) -> None:
        messages = {
            "authentication_failed": (
                "A chave do Hardcover está inválida. Cadastre uma nova em Configuração."
            ),
            "hardcover_authentication_failed": (
                "A chave do Hardcover está inválida. Cadastre uma nova em Configuração."
            ),
            "hardcover_token_unavailable": "Cadastre uma chave nova do Hardcover em Configuração.",
            "remote_unavailable": (
                "Não foi possível acessar o Hardcover. Verifique a conexão e tente de novo."
            ),
            "invalid_response": "O Hardcover retornou dados que não puderam ser lidos.",
            "write_unconfirmed": (
                "O Hardcover não confirmou a alteração. Atualize antes de tentar novamente."
            ),
        }
        self.status_label.setText(messages.get(reason_code, f"A operação falhou ({reason_code})."))

    def _update_sync_confirmation(self) -> None:
        history = getattr(self.operations, "history", None)
        if history is None:
            self.sync_confirmation_label.setText(
                "O KOReader envia o progresso pelo Wi-Fi; "
                "esta tela mostra o valor da última consulta."
            )
            return
        try:
            record = history.get("hardcover_library_refresh")
        except Exception:
            record = None
        explanation = (
            "O progresso vem do KOReader via Wi-Fi; esta estante mostra a última consulta."
        )
        if record is None or record.last_success_at is None:
            if record is not None and record.status == "failed":
                self.sync_confirmation_label.setText(
                    "Estante sem consulta confirmada. Verifique a conexão e tente atualizar. "
                    + explanation
                )
            elif record is not None and record.status == "running":
                self.sync_confirmation_label.setText("Consultando o Hardcover…")
            else:
                self.sync_confirmation_label.setText(
                    "Estante ainda não consultada nesta instalação. " + explanation
                )
            return
        try:
            when = (
                datetime.fromisoformat(record.last_success_at)
                .astimezone()
                .strftime("%d/%m/%Y às %H:%M")
            )
        except TypeError, ValueError, OverflowError:
            when = "horário indisponível"
        suffix = (
            " · a última tentativa falhou; tente atualizar" if record.status == "failed" else ""
        )
        self.sync_confirmation_label.setText(
            f"Última consulta confirmada pelo Hardcover: {when}{suffix}. {explanation}"
        )

    @staticmethod
    def _active_read(entry: HardcoverLibraryEntry) -> HardcoverRead | None:
        return next((read for read in reversed(entry.reads) if read.finished_at is None), None)

    @classmethod
    def _progress_text(cls, entry: HardcoverLibraryEntry, read: HardcoverRead) -> str:
        page = read.progress_pages
        started = (
            f"início {cls._format_date(read.started_at)}" if read.started_at else "início sem data"
        )
        if not entry.edition_pages:
            return f"Página {page} · {started} · total de páginas indisponível"
        percentage = min(100, round(page / entry.edition_pages * 100))
        return (
            f"{percentage}% · página {page} de {entry.edition_pages} · {started} · "
            "percentual estimado pela edição"
        )

    @staticmethod
    def _format_date(value: date | None) -> str:
        return value.strftime("%d/%m/%Y") if value else "sem data"
