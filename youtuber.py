import os
import queue
import shutil
import ssl
import subprocess
import sys
import threading
import webbrowser
from io import BytesIO
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


try:
	import yt_dlp
except ImportError:  # pragma: no cover - handled at runtime for user guidance
	yt_dlp = None

try:
	import imageio_ffmpeg
except ImportError:  # pragma: no cover - optional dependency
	imageio_ffmpeg = None

try:
	import certifi
except ImportError:  # pragma: no cover - optional dependency
	certifi = None

try:
	import webview
except ImportError:  # pragma: no cover - optional dependency
	webview = None

try:
	from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - handled at runtime for user guidance
	Image = None
	ImageTk = None


class YouTubeDownloaderApp:
	def __init__(self, root: ctk.CTk) -> None:
		self.root = root
		self.root.title("YouTube Downloader FullHD")
		self.root.geometry("1100x700")
		self.root.minsize(1200, 620)

		# Estado de la aplicacion
		self.barraMenu = tk.Menu(self.root)
		self.root.config(menu=self.barraMenu)
		self.fileMenu = tk.Menu(self.barraMenu, tearoff=0)
		self.fileMenu.add_command(label="Salir", command=self.root.quit)
		self.barraMenu.add_cascade(label="Archivo", menu=self.fileMenu)

		self.results = []
		self.output_queue: queue.Queue = queue.Queue()
		self.downloading = False
		self.searching = False
		self.loading_more = False
		self.current_query = ""
		self.current_limit = 0
		self.page_step = 20
		self.has_more_results = False
		self.load_more_btn = None
		self.checked_items = set()
		self.thumbnail_images = {}
		self.placeholder_thumb = None
		self.ffmpeg_location = self._resolve_ffmpeg_location()
		self.ca_bundle_path = self._configure_ssl_certificates()
		self.search_mode = tk.StringVar(value="normal")  # "normal" o "playlist"
		self.last_placeholder = "Buscar videos..."  # guardar el placeholder anterior
		self.search_mode.trace_add("write", lambda *args: self._update_search_placeholder())
		self.download_format_var = tk.StringVar(value="mp4")

		self._build_ui()
		self._poll_queue()

		if yt_dlp is None:
			self._set_controls_state("disabled")
			messagebox.showerror(
				"Dependencia faltante",
				"No se encontro el paquete 'yt-dlp'.\n\n"
				"Instalalo con:\n"
				"pip install yt-dlp",
			)
		elif Image is None:
			self._log("Pillow no esta instalado: no se mostraran miniaturas.")
		if self.ca_bundle_path:
			self._log(f"Certificados SSL configurados: {self.ca_bundle_path}")
		elif certifi is None:
			self._log("Sugerencia: instala certifi para evitar errores SSL (pip install certifi).")
		if self.ffmpeg_location:
			self._log(f"ffmpeg detectado: {self.ffmpeg_location}")
		else:
			self._log("ffmpeg no detectado: para mejor opcion 1080p instala ffmpeg o imageio-ffmpeg.")
		if webview is None:
			self._log("Sugerencia: instala pywebview para reproductor embebido (pip install pywebview).")

	def _configure_ssl_certificates(self) -> Optional[str]:
		if certifi is None:
			return None
		try:
			ca_bundle_path = certifi.where()
		except Exception:
			return None
		if not ca_bundle_path or not os.path.exists(ca_bundle_path):
			return None
		os.environ["SSL_CERT_FILE"] = ca_bundle_path
		os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle_path
		return ca_bundle_path

	@staticmethod
	def _is_ssl_certificate_error(exc: Exception) -> bool:
		message = str(exc).lower()
		return (
			"certificate_verify_failed" in message
			or "certificateverifyerror" in message
			or "unable to get local issuer certificate" in message
		)

	def _resolve_ffmpeg_location(self) -> Optional[str]:
		system_ffmpeg = shutil.which("ffmpeg")
		if system_ffmpeg:
			return system_ffmpeg
		if imageio_ffmpeg is None:
			return None
		try:
			return imageio_ffmpeg.get_ffmpeg_exe()
		except Exception:
			return None

	def _build_ui(self) -> None:
		self.root.columnconfigure(0, weight=1)
		self.root.rowconfigure(1, weight=1)

		top_frame = ctk.CTkFrame(self.root, fg_color="transparent")
		top_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
		top_frame.rowconfigure(1, minsize=2)
		top_frame.columnconfigure(1, weight=1)
		top_frame.grid_columnconfigure(0, weight=0)

		# Radio buttons para seleccionar tipo de busqueda
		mode_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
		mode_frame.grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))
		ctk.CTkLabel(mode_frame, text="Tipo de busqueda:").pack(side="left", padx=(0, 8))
		ctk.CTkRadioButton(
			mode_frame,
			text="Busqueda Normal",
			variable=self.search_mode,
			value="normal"
		).pack(side="left", padx=(0, 12))
		ctk.CTkRadioButton(
			mode_frame,
			text="Playlist",
			variable=self.search_mode,
			value="playlist"
		).pack(side="left", padx=(0, 0))

		self.search_input_frame = ctk.CTkFrame(
			top_frame,
			fg_color="#FFFFFF",
			border_width=0.5,
			border_color="#D0D7DE",
		)
		self.search_input_frame.grid(row=1, column=1, sticky="ew")
		self.search_icon = ctk.CTkLabel(
			self.search_input_frame,
			text="\U0001F50D",
			text_color="#6B7280",
			font=("Segoe UI Emoji", 10),
		)
		self.search_icon.pack(side="left", padx=(10, 6), pady=8)
		self.query_entry = ctk.CTkEntry(
			self.search_input_frame,
			font=("Segoe UI", 16),
			fg_color="#FFFFFF",
			text_color="#1F2328",
			border_width=0,
			placeholder_text="Buscar videos...",
		)
		self.query_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)
		self._update_search_placeholder()
		self.search_underline = ctk.CTkFrame(top_frame, height=2, fg_color="#D0D7DE")
		self.search_underline.grid(row=2, column=1, sticky="ew")
		self.query_entry.bind("<Return>", lambda _e: self.search_videos())
		self.query_entry.bind("<FocusIn>", self._on_search_focus_in)
		self.query_entry.bind("<FocusOut>", self._on_search_focus_out)

		self.search_btn = ctk.CTkButton(
			top_frame,
			text="Buscar",
			command=self.search_videos,
		)
		self.search_btn.grid(row=1, column=2, padx=(8, 0))

		self.max_results_var = tk.IntVar(value=20)
		ctk.CTkLabel(top_frame, text="Resultados:").grid(row=1, column=3, padx=(14, 6), sticky="e")
		self.max_results_spin = ttk.Spinbox(
			top_frame,
			from_=5,
			to=50,
			textvariable=self.max_results_var,
			width=5,
		)
		self.max_results_spin.grid(row=1, column=4, sticky="w")

		middle_frame = ctk.CTkFrame(self.root, fg_color="transparent")
		middle_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 0))
		middle_frame.columnconfigure(0, weight=1)
		middle_frame.columnconfigure(1, weight=0)
		middle_frame.rowconfigure(0, weight=1)

		results_frame = ctk.CTkFrame(middle_frame, fg_color="transparent")
		results_frame.grid(row=0, column=0, sticky="nsew")
		results_frame.columnconfigure(0, weight=1)
		results_frame.rowconfigure(1, weight=1)

		# Column layout (pixels): Check, Miniatura, Titulo, Duracion, Canal
		self.col_check_px = 54
		self.col_thumb_px = 112
		self.col_title_px = 350
		self.col_duration_px = 90
		self.col_channel_px = 230

		header = ctk.CTkFrame(results_frame, fg_color="transparent")
		header.grid(row=0, column=0, sticky="ew")
		header.columnconfigure(0, minsize=self.col_check_px, weight=0)
		header.columnconfigure(1, minsize=self.col_thumb_px, weight=0)
		header.columnconfigure(2, minsize=self.col_title_px, weight=0)
		header.columnconfigure(3, minsize=self.col_duration_px, weight=0)
		header.columnconfigure(4, minsize=self.col_channel_px, weight=1)
		ctk.CTkLabel(header, text="Check", anchor="center").grid(row=0, column=0, sticky="ew")
		ctk.CTkLabel(header, text="Miniatura", anchor="center").grid(row=0, column=1, sticky="ew")
		ctk.CTkLabel(header, text="Titulo", anchor="w").grid(row=0, column=2, sticky="ew")
		ctk.CTkLabel(header, text="Duracion", anchor="center").grid(row=0, column=3, sticky="ew")
		ctk.CTkLabel(header, text="Canal", anchor="w").grid(row=0, column=4, sticky="ew")

		body = ctk.CTkFrame(results_frame, fg_color="transparent")
		body.grid(row=1, column=0, sticky="nsew")
		body.columnconfigure(0, weight=1)
		body.rowconfigure(0, weight=1)

		self.results_canvas = tk.Canvas(body, highlightthickness=0, background="#ffffff")
		self.results_canvas.grid(row=0, column=0, sticky="nsew")
		self.y_scroll = ctk.CTkScrollbar(body, orientation="vertical", command=self.results_canvas.yview)
		self.y_scroll.grid(row=0, column=1, sticky="ns")
		self.results_canvas.configure(yscrollcommand=self.y_scroll.set)

		self.rows_frame = ctk.CTkFrame(self.results_canvas, fg_color="#ffffff")
		self.rows_window = self.results_canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
		self.rows_frame.bind("<Configure>", self._on_rows_frame_configure)
		self.results_canvas.bind("<Configure>", self._on_results_canvas_configure)
		self.results_canvas.bind("<MouseWheel>", self._on_results_mousewheel)
		self.results_canvas.bind("<Button-4>", self._on_results_mousewheel)
		self.results_canvas.bind("<Button-5>", self._on_results_mousewheel)
		self.rows_frame.bind("<MouseWheel>", self._on_results_mousewheel)
		self.rows_frame.bind("<Button-4>", self._on_results_mousewheel)
		self.rows_frame.bind("<Button-5>", self._on_results_mousewheel)
		self.row_widgets = {}

		selected_frame = ctk.CTkFrame(middle_frame, fg_color="transparent")
		selected_frame.grid(row=0, column=1, sticky="nsew")
		selected_frame.rowconfigure(2, weight=1)
		selected_frame.columnconfigure(0, weight=1)

		header_frame = ctk.CTkFrame(selected_frame, fg_color="transparent")
		header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
		header_frame.columnconfigure(0, weight=1)

		self.selected_count_var = tk.StringVar(value="Seleccionados: 0")
		ctk.CTkLabel(header_frame, textvariable=self.selected_count_var).grid(row=0, column=0, sticky="w")

		buttons_frame = ctk.CTkFrame(selected_frame, fg_color="transparent")
		buttons_frame.grid(row=1, column=0, sticky="ew", pady=(0, 6))
		buttons_frame.columnconfigure(1, weight=1)

		ctk.CTkButton(
			buttons_frame,
			text="Seleccionar todos",
			command=self._select_all,
			width=110,
			height=24,
		).grid(row=0, column=0, padx=(0, 4))

		ctk.CTkButton(
			buttons_frame,
			text="Deseleccionar todos",
			command=self._deselect_all,
			width=110,
			height=24,
		).grid(row=0, column=1, sticky="w")

		self.selected_text = ctk.CTkTextbox(
			selected_frame,
			width=250,
			font=("Segoe UI", 12),
			height=18,
			wrap="word",
			cursor="arrow",
		)
		self.selected_text.grid(row=2, column=0, sticky="nsew", pady=(0, 0))
		self.selected_text.configure(state="disabled")

		bottom_frame = ctk.CTkFrame(self.root, fg_color="transparent")
		bottom_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
		bottom_frame.columnconfigure(0, weight=1)

		help_text = (
			"Selecciona uno o varios videos y presiona Descargar seleccionados. "
			"Para convertir y unir audio/video en MP4 1080p, instala ffmpeg."
		)
		ctk.CTkLabel(bottom_frame, text=help_text, text_color="#333").grid(
			row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
		)

		self.download_btn = ctk.CTkButton(
			bottom_frame,
			text="Descargar seleccionados (FullHD)",
			command=self.download_selected,
		)
		self.download_btn.grid(row=1, column=0, sticky="w")

		format_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
		format_frame.grid(row=1, column=1, sticky="w", padx=(12, 8))
		ctk.CTkLabel(format_frame, text="Formato:").pack(side="left", padx=(0, 8))
		self.format_mp4_rb = ctk.CTkRadioButton(
			format_frame,
			text="MP4",
			variable=self.download_format_var,
			value="mp4",
		)
		self.format_mp4_rb.pack(side="left", padx=(0, 8))
		self.format_mp3_rb = ctk.CTkRadioButton(
			format_frame,
			text="MP3",
			variable=self.download_format_var,
			value="mp3",
		)
		self.format_mp3_rb.pack(side="left")

		self.status_var = tk.StringVar(value="Listo")
		ctk.CTkLabel(bottom_frame, textvariable=self.status_var).grid(row=1, column=2, padx=12, sticky="w")

		self.progress = ttk.Progressbar(bottom_frame, mode="determinate", maximum=100)
		self.progress.grid(row=1, column=3, sticky="ew")
		bottom_frame.columnconfigure(3, weight=1)

		self.log_text = ctk.CTkTextbox(self.root, height=160, wrap="word")
		self.log_text.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
		self.log_text.configure(state="disabled")
		self.root.rowconfigure(3, weight=0)
		if Image is not None:
			placeholder = Image.new("RGB", (100, 60), color="#202020")
			self.placeholder_thumb = ctk.CTkImage(placeholder, size=(100, 60))

	def _set_controls_state(self, state: str) -> None:
		for widget in (
			self.query_entry,
			self.search_btn,
			self.max_results_spin,
			self.download_btn,
			self.format_mp4_rb,
			self.format_mp3_rb,
		):
			widget.configure(state=state)
		if self.load_more_btn is not None:
			self.load_more_btn.configure(state=state)

	def _start_search_loading(self) -> None:
		self.searching = True
		self.progress.stop()
		self.progress.configure(mode="indeterminate")
		self.progress["value"] = 0
		self.progress.start(10)

	def _stop_search_loading(self) -> None:
		if not self.searching:
			return
		self.searching = False
		self.progress.stop()
		self.progress.configure(mode="determinate", maximum=100)
		self.progress["value"] = 0

	def _update_search_placeholder(self) -> None:
		if not hasattr(self, 'query_entry'):
			return

		is_playlist_mode = self.search_mode.get() == "playlist"
		new_placeholder = "Colocar URL de Playlist" if is_playlist_mode else "Buscar videos..."

		# Al cambiar el modo, dejar el input en estado inicial (vacío + placeholder).
		self.query_entry.delete(0, "end")

		# Actualizar el placeholder
		self.query_entry.configure(placeholder_text=new_placeholder)
		self.last_placeholder = new_placeholder

		# Deshabilitar max_results_spin en modo playlist
		if hasattr(self, 'max_results_spin'):
			state = "disabled" if is_playlist_mode else "normal"
			self.max_results_spin.configure(state=state)

	def _log(self, message: str) -> None:
		self.log_text.configure(state="normal")
		self.log_text.insert("end", f"{message}\n")
		self.log_text.see("end")
		self.log_text.configure(state="disabled")

	def _on_search_focus_in(self, _event=None) -> None:
		self.search_input_frame.configure(border_color="#0A66D1")
		self.search_underline.configure(fg_color="#0A66D1")

	def _on_search_focus_out(self, _event=None) -> None:
		self.search_input_frame.configure(border_color="#D0D7DE")
		self.search_underline.configure(fg_color="#D0D7DE")

	@staticmethod
	def _format_duration(seconds) -> str:
		if not seconds or not isinstance(seconds, (int, float)):
			return "--:--"
		seconds = int(seconds)
		h, rem = divmod(seconds, 3600)
		m, s = divmod(rem, 60)
		if h:
			return f"{h:02d}:{m:02d}:{s:02d}"
		return f"{m:02d}:{s:02d}"

	@staticmethod
	def _format_views(views) -> str:
		if not isinstance(views, int):
			return "-"
		return f"{views:,}".replace(",", ".")

	def search_videos(self) -> None:
		if yt_dlp is None:
			return

		query = self.query_entry.get().strip()
		if not query:
			messagebox.showwarning("Falta texto", "Escribe algo para buscar en YouTube.")
			return

		max_results = self.max_results_var.get()
		if max_results < 1:
			max_results = 20
		self.current_query = query
		self.current_limit = max_results
		self.loading_more = False
		self.status_var.set("Buscando...")
		self._set_controls_state("disabled")
		self._clear_results()
		self._start_search_loading()

		search_mode = self.search_mode.get()
		worker = threading.Thread(
			target=self._search_worker,
			args=(query, max_results, False, set(), search_mode),
			daemon=True,
		)
		worker.start()

	def _search_worker(self, query: str, max_results: int, append: bool, known_ids, search_mode: str = "normal") -> None:
		base_options = {
			"quiet": True,
			"no_warnings": True,
			"extract_flat": True,
			"skip_download": True,
		}
		try:
			if search_mode == "playlist":
				# Si es una URL de playlist, usarla directamente
				if query.startswith("http://") or query.startswith("https://"):
					search_query = query
					base_options["noplaylist"] = False
				else:
					# Si no es URL, buscar por nombre de playlist
					search_query = f"ytplaylist{max_results}:{query}"
			else:
				search_query = f"ytsearch{max_results}:{query}"
			info = None
			for use_insecure in (False, True):
				options = dict(base_options)
				if use_insecure:
					options["nocheckcertificate"] = True
				try:
					with yt_dlp.YoutubeDL(options) as ydl:
						info = ydl.extract_info(search_query, download=False)
					break
				except Exception as exc:
					if not use_insecure and self._is_ssl_certificate_error(exc):
						self.output_queue.put(
							(
								"log",
								"Aviso SSL: no se pudo validar certificado. Reintentando en modo compatible.",
							)
						)
						continue
					raise
			entries = info.get("entries", []) if info else []
			parsed = []
			for item in entries:
				if not item:
					continue
				video_id = item.get("id")
				thumbnails = item.get("thumbnails") or []
				thumbnail_url = item.get("thumbnail")
				if not thumbnail_url and thumbnails:
					thumbnail_url = thumbnails[-1].get("url")
				parsed.append(
					{
						"id": video_id,
						"title": item.get("title") or "Sin titulo",
						"channel": item.get("uploader") or item.get("channel") or "Canal desconocido",
						"duration": self._format_duration(item.get("duration")),
						"views": self._format_views(item.get("view_count")),
						"thumbnail_url": thumbnail_url,
						"url": item.get("url")
						or item.get("webpage_url")
						or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None),
					}
				)

			if append:
				new_items = []
				for video in parsed:
					vid = video.get("id")
					if vid and vid in known_ids:
						continue
					new_items.append(video)
				self.output_queue.put(("search_append_done", (new_items, len(parsed), max_results)))
			else:
				self.output_queue.put(("search_done", (parsed, len(parsed), query, max_results)))
		except Exception as exc:  # pragma: no cover - depends on network/runtime
			self.output_queue.put(("error", f"Error de busqueda: {exc}"))

	def _clear_results(self) -> None:
		self.results = []
		self.checked_items.clear()
		self.thumbnail_images.clear()
		self.has_more_results = False
		self.selected_count_var.set("Seleccionados: 0")
		self.selected_text.configure(state="normal")
		self.selected_text.delete("1.0", "end")
		self.selected_text.configure(state="disabled")
		for child in self.rows_frame.winfo_children():
			child.destroy()
		self.row_widgets.clear()
		self.load_more_btn = None
		self.results_canvas.yview_moveto(0)

	def _render_load_more_button(self) -> None:
		if self.load_more_btn is not None:
			self.load_more_btn.destroy()
			self.load_more_btn = None
		if not self.has_more_results:
			return
		row = ctk.CTkFrame(self.rows_frame, fg_color="#ffffff", corner_radius=0)
		row.grid(row=len(self.results), column=0, sticky="ew", pady=(8, 12))
		row.columnconfigure(0, weight=1)
		self.load_more_btn = ctk.CTkButton(
			row,
			text="Cargar 20 mas",
			command=self.load_more_results,
		)
		self.load_more_btn.grid(row=0, column=0)
		if self.loading_more:
			self.load_more_btn.configure(state="disabled")

	def load_more_results(self) -> None:
		if yt_dlp is None:
			return
		if self.searching or self.loading_more or not self.current_query:
			return
		self.loading_more = True
		self.status_var.set("Cargando mas resultados...")
		if self.load_more_btn is not None:
			self.load_more_btn.configure(state="disabled", text="Cargando...")
		known_ids = {item.get("id") for item in self.results if item.get("id")}
		next_limit = self.current_limit + self.page_step
		search_mode = self.search_mode.get()
		worker = threading.Thread(
			target=self._search_worker,
			args=(self.current_query, next_limit, True, known_ids, search_mode),
			daemon=True,
		)
		worker.start()

	def _on_rows_frame_configure(self, _event=None) -> None:
		self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))

	def _on_results_canvas_configure(self, event) -> None:
		self.results_canvas.itemconfigure(self.rows_window, width=event.width)

	def _on_results_mousewheel(self, event):
		if getattr(event, "num", None) == 4:
			delta_units = -1
		elif getattr(event, "num", None) == 5:
			delta_units = 1
		else:
			# macOS often sends small deltas (e.g. +/-1 or +/-2), while Windows sends multiples of 120.
			delta = int(getattr(event, "delta", 0))
			if not delta:
				delta_units = 0
			elif sys.platform == "darwin":
				delta_units = -1 if delta > 0 else 1
			elif abs(delta) < 120:
				delta_units = -1 if delta > 0 else 1
			else:
				delta_units = -int(delta / 120)
		if delta_units:
			self.results_canvas.yview_scroll(delta_units, "units")
		return "break"

	def _select_all(self) -> None:
		if not self.results:
			return
		for idx in range(len(self.results)):
			iid = str(idx)
			self.checked_items.add(iid)
			widgets = self.row_widgets.get(iid)
			if widgets and widgets.get("check_var"):
				widgets["check_var"].set(True)
		self._refresh_selected_panel()

	def _deselect_all(self) -> None:
		for iid in list(self.checked_items):
			widgets = self.row_widgets.get(iid)
			if widgets and widgets.get("check_var"):
				widgets["check_var"].set(False)
		self.checked_items.clear()
		self._refresh_selected_panel()

	def _refresh_selected_panel(self) -> None:
		self.selected_text.configure(state="normal")
		self.selected_text.delete("1.0", "end")
		ordered_ids = sorted(self.checked_items, key=int)
		for item_id in ordered_ids:
			idx = int(item_id)
			if idx >= len(self.results):
				continue
			video = self.results[idx]
			title = video.get("title", "Sin titulo")
			duration = video.get("duration", "--:--")
			self.selected_text.insert("end", f"{title} ({duration})\n\n")
		self.selected_text.configure(state="disabled")
		self.selected_count_var.set(f"Seleccionados: {len(ordered_ids)}")

	def _on_native_selection_changed(self, _event=None) -> None:
		for iid, widgets in self.row_widgets.items():
			check_var = widgets.get("check_var")
			if check_var is None:
				continue
			should_be_checked = iid in self.checked_items
			if bool(check_var.get()) != should_be_checked:
				check_var.set(should_be_checked)

	def _on_checkbutton_toggle(self, iid: str, check_var: tk.BooleanVar) -> None:
		if check_var.get():
			self.checked_items.add(iid)
		else:
			self.checked_items.discard(iid)
		self._refresh_selected_panel()

	def _toggle_checked(self, iid: str) -> None:
		if iid in self.checked_items:
			self.checked_items.remove(iid)
		else:
			self.checked_items.add(iid)
		self._on_native_selection_changed()
		self._refresh_selected_panel()

	def _on_row_click_toggle(self, iid: str, _event=None):
		self._toggle_checked(iid)
		return "break"

	def _on_url_click(self, iid: str, _event=None):
		idx = int(iid)
		if 0 <= idx < len(self.results):
			url = self.results[idx].get("url")
			if url:
				title = self.results[idx].get("title") or "Video"
				self._open_video_in_webview(url, title)
		return "break"

	def _open_video_in_webview(self, url: str, title: str) -> None:
		cmd = []
		if getattr(sys, "frozen", False):
			cmd = [sys.executable, "--webview-player", url, title]
		else:
			cmd = [sys.executable, os.path.abspath(__file__), "--webview-player", url, title]
		try:
			subprocess.Popen(cmd)
		except Exception as exc:
			messagebox.showwarning(
				"Reproductor no disponible",
				f"No se pudo abrir el reproductor embebido ({exc}). Se abrira en navegador.",
			)
			webbrowser.open_new_tab(url)

	def _render_results(self, results) -> None:
		self._clear_results()
		self.results = list(results)
		self.has_more_results = len(results) >= self.current_limit
		for idx, item in enumerate(results):
			iid = str(idx)
			row = ctk.CTkFrame(self.rows_frame, fg_color="#ffffff", corner_radius=0)
			row.grid(row=idx, column=0, sticky="ew")
			row.columnconfigure(0, minsize=self.col_check_px, weight=0)
			row.columnconfigure(1, minsize=self.col_thumb_px, weight=0)
			row.columnconfigure(2, minsize=self.col_title_px, weight=0)
			row.columnconfigure(3, minsize=self.col_duration_px, weight=0)
			row.columnconfigure(4, minsize=self.col_channel_px, weight=1)

			check_var = tk.BooleanVar(value=False)
			check_btn = ctk.CTkCheckBox(
				row,
				text="",
				variable=check_var,
				command=lambda rid=iid, var=check_var: self._on_checkbutton_toggle(rid, var),
				width=10,
			)
			check_btn.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(4, 2))

			thumb_lbl = ctk.CTkLabel(row, image=self.placeholder_thumb, text="")
			thumb_lbl.grid(row=0, column=1, rowspan=2, padx=(2, 6), pady=4, sticky="n")

			title_lbl = ctk.CTkLabel(
				row,
				text=item.get("title") or "Sin titulo",
				text_color="#000000",
				anchor="w",
				justify="left",
				wraplength=self.col_title_px - 8,
				fg_color="transparent",
			)
			title_lbl.grid(row=0, column=2, sticky="ew", pady=(4, 0))

			url_text = item.get("url") or ""
			url_lbl = ctk.CTkLabel(
				row,
				text=url_text,
				text_color="#0b57d0",
				cursor="hand2",
				anchor="w",
				justify="left",
				wraplength=self.col_title_px - 8,
				fg_color="transparent",
			)
			url_lbl.grid(row=1, column=2, sticky="ew", pady=(0, 4))

			duration_lbl = ctk.CTkLabel(
				row,
				text=item["duration"],
				text_color="#000000",
				anchor="center",
				width=80,
				fg_color="transparent",
			)
			duration_lbl.grid(row=0, column=3, rowspan=2, sticky="n", pady=(18, 0), padx=(0, 6))

			channel_lbl = ctk.CTkLabel(
				row,
				text=item["channel"],
				text_color="#000000",
				anchor="w",
				fg_color="transparent",
			)
			channel_lbl.grid(row=0, column=4, rowspan=2, sticky="w", padx=(6, 6))

			for widget in (row, thumb_lbl, title_lbl, duration_lbl, channel_lbl):
				widget.bind("<Button-1>", lambda e, rid=iid: self._on_row_click_toggle(rid, e))
				widget.bind("<MouseWheel>", self._on_results_mousewheel)
				widget.bind("<Button-4>", self._on_results_mousewheel)
				widget.bind("<Button-5>", self._on_results_mousewheel)
			url_lbl.bind("<Button-1>", lambda e, rid=iid: self._on_url_click(rid, e))
			url_lbl.bind("<MouseWheel>", self._on_results_mousewheel)
			url_lbl.bind("<Button-4>", self._on_results_mousewheel)
			url_lbl.bind("<Button-5>", self._on_results_mousewheel)
			check_btn.bind("<MouseWheel>", self._on_results_mousewheel)
			check_btn.bind("<Button-4>", self._on_results_mousewheel)
			check_btn.bind("<Button-5>", self._on_results_mousewheel)

			self.row_widgets[iid] = {
				"check": check_btn,
				"check_var": check_var,
				"thumb": thumb_lbl,
			}
		self._render_load_more_button()
		if Image is not None and ImageTk is not None:
			threading.Thread(target=self._thumbnail_worker, args=(results,), daemon=True).start()

	def _append_results(self, new_results) -> None:
		start_idx = len(self.results)
		self.results.extend(new_results)
		for offset, item in enumerate(new_results):
			idx = start_idx + offset
			iid = str(idx)
			row = ctk.CTkFrame(self.rows_frame, fg_color="#ffffff", corner_radius=0)
			row.grid(row=idx, column=0, sticky="ew")
			row.columnconfigure(0, minsize=self.col_check_px, weight=0)
			row.columnconfigure(1, minsize=self.col_thumb_px, weight=0)
			row.columnconfigure(2, minsize=self.col_title_px, weight=0)
			row.columnconfigure(3, minsize=self.col_duration_px, weight=0)
			row.columnconfigure(4, minsize=self.col_channel_px, weight=1)

			check_var = tk.BooleanVar(value=False)
			check_btn = ctk.CTkCheckBox(
				row,
				text="",
				variable=check_var,
				command=lambda rid=iid, var=check_var: self._on_checkbutton_toggle(rid, var),
				width=10,
			)
			check_btn.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(4, 2))

			thumb_lbl = ctk.CTkLabel(row, image=self.placeholder_thumb, text="")
			thumb_lbl.grid(row=0, column=1, rowspan=2, padx=(2, 6), pady=4, sticky="n")

			title_lbl = ctk.CTkLabel(
				row,
				text=item.get("title") or "Sin titulo",
				text_color="#000000",
				anchor="w",
				justify="left",
				wraplength=self.col_title_px - 8,
				fg_color="transparent",
			)
			title_lbl.grid(row=0, column=2, sticky="ew", pady=(4, 0))

			url_text = item.get("url") or ""
			url_lbl = ctk.CTkLabel(
				row,
				text=url_text,
				text_color="#0b57d0",
				cursor="hand2",
				anchor="w",
				justify="left",
				wraplength=self.col_title_px - 8,
				fg_color="transparent",
			)
			url_lbl.grid(row=1, column=2, sticky="ew", pady=(0, 4))

			duration_lbl = ctk.CTkLabel(
				row,
				text=item["duration"],
				text_color="#000000",
				anchor="center",
				width=80,
				fg_color="transparent",
			)
			duration_lbl.grid(row=0, column=3, rowspan=2, sticky="n", pady=(18, 0), padx=(0, 6))

			channel_lbl = ctk.CTkLabel(
				row,
				text=item["channel"],
				text_color="#000000",
				anchor="w",
				fg_color="transparent",
			)
			channel_lbl.grid(row=0, column=4, rowspan=2, sticky="w", padx=(6, 6))

			for widget in (row, thumb_lbl, title_lbl, duration_lbl, channel_lbl):
				widget.bind("<Button-1>", lambda e, rid=iid: self._on_row_click_toggle(rid, e))
				widget.bind("<MouseWheel>", self._on_results_mousewheel)
				widget.bind("<Button-4>", self._on_results_mousewheel)
				widget.bind("<Button-5>", self._on_results_mousewheel)
			url_lbl.bind("<Button-1>", lambda e, rid=iid: self._on_url_click(rid, e))
			url_lbl.bind("<MouseWheel>", self._on_results_mousewheel)
			url_lbl.bind("<Button-4>", self._on_results_mousewheel)
			url_lbl.bind("<Button-5>", self._on_results_mousewheel)
			check_btn.bind("<MouseWheel>", self._on_results_mousewheel)
			check_btn.bind("<Button-4>", self._on_results_mousewheel)
			check_btn.bind("<Button-5>", self._on_results_mousewheel)

			self.row_widgets[iid] = {
				"check": check_btn,
				"check_var": check_var,
				"thumb": thumb_lbl,
			}
		self._render_load_more_button()
		if Image is not None and ImageTk is not None and new_results:
			threading.Thread(target=self._thumbnail_worker, args=(new_results, start_idx), daemon=True).start()

	def _thumbnail_worker(self, results, idx_offset: int = 0) -> None:
		for idx, item in enumerate(results):
			thumb_url = item.get("thumbnail_url")
			if not thumb_url:
				continue
			try:
				if self.ca_bundle_path:
					ssl_context = ssl.create_default_context(cafile=self.ca_bundle_path)
					with urlopen(thumb_url, timeout=12, context=ssl_context) as response:
						thumb_data = response.read()
				else:
					with urlopen(thumb_url, timeout=12) as response:
						thumb_data = response.read()
				
				if thumb_data:
					self.output_queue.put(("thumbnail", (idx + idx_offset, thumb_data)))
			except Exception:
				continue

	def _apply_thumbnail(self, idx: int, thumb_data: bytes) -> None:
		if Image is None or ImageTk is None:
			return
		iid = str(idx)
		widgets = self.row_widgets.get(iid)
		if not widgets:
			return
		try:
			image = Image.open(BytesIO(thumb_data)).convert("RGB")
			canvas = Image.new("RGB", (100, 60), color="#111111")
			image.thumbnail((100, 60), Image.Resampling.LANCZOS)
			offset_x = (100 - image.width) // 2
			offset_y = (60 - image.height) // 2
			canvas.paste(image, (offset_x, offset_y))
			thumb_photo = ctk.CTkImage(canvas, size=(100, 60))
			self.thumbnail_images[iid] = thumb_photo
			widgets["thumb"].configure(image=thumb_photo)
		except Exception:
			return

	def download_selected(self) -> None:
		if yt_dlp is None:
			return
		if self.downloading:
			return

		selected = sorted(self.checked_items, key=int)
		if not selected:
			messagebox.showinfo("Sin seleccion", "Selecciona al menos un video para descargar.")
			return

		folder = filedialog.askdirectory(title="Selecciona la carpeta de descarga")
		if not folder:
			return

		urls = []
		for item_id in selected:
			video = self.results[int(item_id)]
			if video.get("url"):
				urls.append(video["url"])

		if not urls:
			messagebox.showerror("Sin URL", "No se encontraron URLs validas para descargar.")
			return

		self.downloading = True
		download_format = self.download_format_var.get().strip().lower()
		if download_format not in ("mp3", "mp4"):
			download_format = "mp4"
		self.progress.stop()
		self.progress.configure(mode="determinate", maximum=100)
		self.progress["value"] = 0
		self.status_var.set(f"Descargando ({download_format.upper()})...")
		self._set_controls_state("disabled")
		self._log(
			f"Iniciando descarga de {len(urls)} elemento(s) en formato {download_format.upper()} en: {folder}"
		)

		worker = threading.Thread(target=self._download_worker, args=(urls, folder, download_format), daemon=True)
		worker.start()

	def _download_worker(self, urls, folder: str, output_format: str) -> None:
		total = len(urls)
		current_index = {"value": 0}
		has_ffmpeg = self.ffmpeg_location is not None
		if not has_ffmpeg:
			self.output_queue.put(
				(
					"error",
					"No se encontro ffmpeg. Instala ffmpeg o imageio-ffmpeg para convertir/combinar audio y video.",
				)
			)
			return

		def progress_hook(data):
			status = data.get("status")
			if status == "downloading":
				downloaded = data.get("downloaded_bytes", 0)
				total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
				per_video = (downloaded / total_bytes * 100) if total_bytes else 0
				global_pct = ((current_index["value"] + (per_video / 100)) / total) * 100
				self.output_queue.put(("progress", global_pct))
			elif status == "finished":
				self.output_queue.put(("log", f"Completado: {os.path.basename(data.get('filename', 'archivo'))}"))

		base_options = {
			"outtmpl": os.path.join(folder, "%(title).200B.%(ext)s"),
			"noplaylist": True,
			"progress_hooks": [progress_hook],
			"quiet": True,
			"no_warnings": True,
			"retries": 5,
			"ffmpeg_location": self.ffmpeg_location,
		}

		if output_format == "mp3":
			base_options.update(
				{
					"format": "bestaudio/best",
					"postprocessors": [
						{
							"key": "FFmpegExtractAudio",
							"preferredcodec": "mp3",
							"preferredquality": "320",
						}
					],
				}
			)
		else:
			# Prioriza 1080p y codecs compatibles en contenedor MP4.
			format_selector = (
				"bestvideo[height=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
				"bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
				"bestvideo[height=1080]+bestaudio/"
				"bestvideo[height<=1080]+bestaudio/"
				"best[height=1080][ext=mp4]/best[height<=1080][ext=mp4]"
			)
			base_options.update(
				{
					"format": format_selector,
					"merge_output_format": "mp4",
					"postprocessor_args": {
						"Merger": ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
					},
				}
			)

		try:
			for use_insecure in (False, True):
				options = dict(base_options)
				if use_insecure:
					options["nocheckcertificate"] = True
				try:
					with yt_dlp.YoutubeDL(options) as ydl:
						for i, url in enumerate(urls):
							current_index["value"] = i
							self.output_queue.put(("log", f"Descargando ({i + 1}/{total}): {url}"))
							ydl.download([url])
					break
				except Exception as exc:
					if not use_insecure and self._is_ssl_certificate_error(exc):
						self.output_queue.put(
							(
								"log",
								"Aviso SSL: no se pudo validar certificado. Reintentando en modo compatible.",
							)
						)
						continue
					raise
			self.output_queue.put(("progress", 100))
			self.output_queue.put(("download_done", None))
		except Exception as exc:  # pragma: no cover - depends on network/runtime
			self.output_queue.put(("error", f"Error de descarga: {exc}"))

	def _poll_queue(self) -> None:
		try:
			while True:
				item_type, payload = self.output_queue.get_nowait()
				if item_type == "search_done":
					results, found_count, query, limit = payload
					self._stop_search_loading()
					self.current_query = query
					self.current_limit = limit
					self.has_more_results = found_count >= limit
					self._render_results(results)
					self.status_var.set(f"Resultados: {len(results)}")
					self._log(f"Busqueda completada. Videos encontrados: {len(results)}")
					self._set_controls_state("normal")
				elif item_type == "search_append_done":
					new_results, found_count, new_limit = payload
					self.loading_more = False
					self.current_limit = new_limit
					self.has_more_results = found_count >= new_limit
					if new_results:
						self._append_results(new_results)
						self.status_var.set(f"Se agregaron {len(new_results)} resultados")
						self._log(f"Paginacion: +{len(new_results)} resultados")
					else:
						self._render_load_more_button()
						self.status_var.set("No hay mas resultados")
						self._log("Paginacion: sin resultados nuevos")
					self._set_controls_state("normal")
				elif item_type == "progress":
					self.progress["value"] = max(0, min(100, payload))
				elif item_type == "download_done":
					self.downloading = False
					self.status_var.set("Descarga completada")
					self._log("Descarga finalizada correctamente.")
					self._set_controls_state("normal")
					messagebox.showinfo("Finalizado", "Se completaron todas las descargas.")
				elif item_type == "log":
					self._log(payload)
				elif item_type == "thumbnail":
					idx, thumb_data = payload
					self._apply_thumbnail(idx, thumb_data)
				elif item_type == "error":
					self._stop_search_loading()
					self.downloading = False
					self.loading_more = False
					self.status_var.set("Error")
					self._log(payload)
					self._set_controls_state("normal")
					self._render_load_more_button()
					messagebox.showerror("Error", payload)
		except queue.Empty:
			pass
		finally:
			self.root.after(120, self._poll_queue)


def main() -> None:
	ctk.set_appearance_mode("light")
	ctk.set_default_color_theme("blue")
	root = ctk.CTk()
	app = YouTubeDownloaderApp(root)
	try:
		root.mainloop()
	except KeyboardInterrupt:
		try:
			root.destroy()
		except tk.TclError:
			pass


def _extract_youtube_video_id(url: str) -> Optional[str]:
	parsed = urlparse(url)
	host = parsed.netloc.lower()
	path = parsed.path.strip("/")

	if "youtu.be" in host:
		return path.split("/")[0] if path else None
	if "youtube.com" in host:
		if parsed.path == "/watch":
			return parse_qs(parsed.query).get("v", [None])[0]
		if parsed.path.startswith("/shorts/"):
			return path.split("/")[1] if len(path.split("/")) > 1 else None
		if parsed.path.startswith("/embed/"):
			return path.split("/")[1] if len(path.split("/")) > 1 else None
	return None


def _normalize_youtube_watch_url(url: str) -> str:
	v_id = _extract_youtube_video_id(url)
	if not v_id:
		return url
	return f"https://www.youtube.com/watch?v={v_id}"


def run_webview_player(url: str, title: str) -> int:
	if not url:
		return 1
	normalized_url = _normalize_youtube_watch_url(url)
	if webview is None:
		webbrowser.open_new_tab(normalized_url)
		return 0
	window_title = f"Reproduciendo: {title[:70]}" if title else "Reproduciendo"
	try:
		webview.create_window(window_title, url=normalized_url, width=980, height=620)
		webview.start()
	except Exception:
		webbrowser.open_new_tab(normalized_url)
	return 0


if __name__ == "__main__":
	if len(sys.argv) > 1 and sys.argv[1] == "--webview-player":
		url_arg = sys.argv[2] if len(sys.argv) > 2 else ""
		title_arg = sys.argv[3] if len(sys.argv) > 3 else "Video"
		raise SystemExit(run_webview_player(url_arg, title_arg))
	main()
