import os
import queue
import shutil
import ssl
import sys
import threading
import webbrowser
from io import BytesIO
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from urllib.request import urlopen


try:
	import yt_dlp
except ImportError:  # pragma: no cover - handled at runtime for user guidance
	yt_dlp = None

try:
	import vlc
except Exception:
	vlc = None

try:
	import imageio_ffmpeg
except ImportError:  # pragma: no cover - optional dependency
	imageio_ffmpeg = None

try:
	import certifi
except ImportError:  # pragma: no cover - optional dependency
	certifi = None

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

		ctk.CTkLabel(top_frame, text="Buscar:").grid(row=0, column=0, padx=(0, 8), sticky="w")
		self.search_input_frame = ctk.CTkFrame(
			top_frame,
			fg_color="#FFFFFF",
			border_width=0.5,
			border_color="#D0D7DE",
		)
		self.search_input_frame.grid(row=0, column=1, sticky="ew")
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
		self.search_underline = ctk.CTkFrame(top_frame, height=2, fg_color="#D0D7DE")
		self.search_underline.grid(row=1, column=1, sticky="ew")
		self.query_entry.bind("<Return>", lambda _e: self.search_videos())
		self.query_entry.bind("<FocusIn>", self._on_search_focus_in)
		self.query_entry.bind("<FocusOut>", self._on_search_focus_out)

		self.search_btn = ctk.CTkButton(
			top_frame,
			text="Buscar",
			command=self.search_videos,
		)
		self.search_btn.grid(row=0, column=2, padx=(8, 0))

		self.max_results_var = tk.IntVar(value=20)
		ctk.CTkLabel(top_frame, text="Resultados:").grid(row=0, column=3, padx=(14, 6), sticky="e")
		self.max_results_spin = ttk.Spinbox(
			top_frame,
			from_=5,
			to=50,
			textvariable=self.max_results_var,
			width=5,
		)
		self.max_results_spin.grid(row=0, column=4, sticky="w")

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
		selected_frame.rowconfigure(1, weight=1)
		selected_frame.columnconfigure(0, weight=1)

		self.selected_count_var = tk.StringVar(value="Seleccionados: 0")
		ctk.CTkLabel(selected_frame, textvariable=self.selected_count_var).grid(row=0, column=0, sticky="w")

		self.selected_text = ctk.CTkTextbox(
			selected_frame,
			width=250,
			font=("Segoe UI", 8),
			height=18,
			wrap="word",
			cursor="arrow",
		)
		self.selected_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
		self.selected_text.configure(state="disabled")

		selected_scroll = ctk.CTkScrollbar(selected_frame, orientation="vertical", command=self.selected_text.yview)
		selected_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
		self.selected_text.configure(yscrollcommand=selected_scroll.set)

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

		self.status_var = tk.StringVar(value="Listo")
		ctk.CTkLabel(bottom_frame, textvariable=self.status_var).grid(row=1, column=1, padx=12, sticky="w")

		self.progress = ttk.Progressbar(bottom_frame, mode="determinate", maximum=100)
		self.progress.grid(row=1, column=2, sticky="ew")
		bottom_frame.columnconfigure(2, weight=1)

		self.log_text = ctk.CTkTextbox(self.root, height=160, wrap="word")
		self.log_text.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
		self.log_text.configure(state="disabled")
		self.root.rowconfigure(3, weight=0)
		if Image is not None and ImageTk is not None:
			placeholder = Image.new("RGB", (100, 60), color="#202020")
			self.placeholder_thumb = ImageTk.PhotoImage(placeholder)

	def _set_controls_state(self, state: str) -> None:
		for widget in (self.query_entry, self.search_btn, self.max_results_spin, self.download_btn):
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

		worker = threading.Thread(
			target=self._search_worker,
			args=(query, max_results, False, set()),
			daemon=True,
		)
		worker.start()

	def _search_worker(self, query: str, max_results: int, append: bool, known_ids) -> None:
		base_options = {
			"quiet": True,
			"no_warnings": True,
			"extract_flat": True,
			"skip_download": True,
		}
		try:
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
		worker = threading.Thread(
			target=self._search_worker,
			args=(self.current_query, next_limit, True, known_ids),
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
				self._open_video_popup(url, self.results[idx].get("title") or "Video")
		return "break"

	def _open_video_popup(self, url: str, title: str) -> None:
		if vlc is None:
			messagebox.showwarning(
				"Reproductor no disponible",
				"No se pudo cargar VLC embebido. Se abrira en el navegador.",
			)
			webbrowser.open_new_tab(url)
			return

		popup = tk.Toplevel(self.root)
		popup.title(f"Reproduciendo: {title[:70]}")
		popup.geometry("450x350")
		popup.minsize(450, 350)

		video_frame = tk.Frame(popup, bg="#000000")
		video_frame.pack(fill="both", expand=True)

		controls = ttk.Frame(popup)
		controls.pack(fill="x")

		status_var = tk.StringVar(value="Preparando reproduccion...")
		ttk.Label(controls, textvariable=status_var).pack(side="left", padx=8, pady=6)

		progress_var = tk.DoubleVar(value=0.0)
		progress_scale = ttk.Scale(controls, from_=0, to=1000, orient="horizontal", variable=progress_var)
		progress_scale.pack(side="left", fill="x", expand=True, padx=(6, 8), pady=6)
		progress_scale.configure(state="disabled")

		instance = vlc.Instance(
			"--no-video-title-show",
			"--avcodec-hw=none",
			"--quiet",
			"--verbose=0",
		)
		player = instance.media_player_new()
		is_dragging = {"value": False}

		popup.update_idletasks()
		handle = video_frame.winfo_id()
		player.set_hwnd(handle)

		def start_playback(target_url: str) -> None:
			try:
				player.set_mrl(target_url)
				player.play()
				status_var.set("Reproduciendo")
				pause_btn.configure(text="Pausar")
				progress_scale.configure(state="normal")
			except Exception as exc:
				status_var.set("No se pudo reproducir")
				messagebox.showerror("Error de reproduccion", str(exc))

		def play_now() -> None:
			try:
				state = player.get_state()
				if state in (vlc.State.Paused, vlc.State.Stopped, vlc.State.Ended):
					player.play()
				status_var.set("Reproduciendo")
				pause_btn.configure(text="Pausar")
			except Exception as exc:
				status_var.set("Error al reproducir")
				messagebox.showerror("Error de reproduccion", str(exc))

		def restart_from_beginning() -> None:
			try:
				player.set_time(0)
				player.play()
				pause_btn.configure(text="Pausar")
				status_var.set("Reiniciado")
			except Exception as exc:
				status_var.set("Error al reiniciar")
				messagebox.showerror("Error de reproduccion", str(exc))

		def toggle_pause() -> None:
			try:
				state = player.get_state()
				if state == vlc.State.Playing:
					player.pause()
					pause_btn.configure(text="Reanudar")
					status_var.set("En pausa")
				elif state == vlc.State.Paused:
					player.pause()
					pause_btn.configure(text="Pausar")
					status_var.set("Reproduciendo")
				else:
					player.play()
					pause_btn.configure(text="Pausar")
					status_var.set("Reproduciendo")
			except Exception as exc:
				status_var.set("Error al pausar/reanudar")
				messagebox.showerror("Error de reproduccion", str(exc))

		def _resolve_stream_url(video_url: str) -> str:
			if yt_dlp is None:
				return video_url
			opts = {
				"quiet": True,
				"no_warnings": True,
				"skip_download": True,
				"noplaylist": True,
				"format": (
					"best[ext=mp4][vcodec!=none][acodec!=none][height<=1080]"
					"/best[ext=mp4][height<=1080]"
					"/best[height<=1080]"
					"/best"
				),
			}
			try:
				with yt_dlp.YoutubeDL(opts) as ydl:
					info = ydl.extract_info(video_url, download=False)
				if not info:
					return video_url
				if info.get("url"):
					return info["url"]
				formats = info.get("formats") or []
				if formats:
					preferred = None
					for fmt in reversed(formats):
						if fmt.get("url") and (fmt.get("vcodec") != "none" or fmt.get("acodec") != "none"):
							preferred = fmt
							break
					if preferred and preferred.get("url"):
						return preferred["url"]
			except Exception:
				return video_url
			return video_url

		def resolve_and_play() -> None:
			status_var.set("Resolviendo stream directo...")
			stream_url = _resolve_stream_url(url)
			if not popup.winfo_exists():
				return
			popup.after(0, lambda: start_playback(stream_url))

		def _on_seek_press(_event=None):
			is_dragging["value"] = True

		def _on_seek_release(_event=None):
			is_dragging["value"] = False
			try:
				length_ms = player.get_length()
				if length_ms and length_ms > 0:
					target_ms = int((progress_var.get() / 1000.0) * length_ms)
					player.set_time(target_ms)
			except Exception:
				pass

		def _on_seek_move(value):
			if not is_dragging["value"]:
				return
			try:
				length_ms = player.get_length()
				if length_ms and length_ms > 0:
					target_ms = int((float(value) / 1000.0) * length_ms)
					player.set_time(target_ms)
			except Exception:
				pass

		def _update_progress_loop() -> None:
			if not popup.winfo_exists():
				return
			try:
				length_ms = player.get_length()
				current_ms = player.get_time()
				if length_ms and length_ms > 0 and current_ms >= 0 and not is_dragging["value"]:
					pos = (current_ms / length_ms) * 1000.0
					progress_var.set(max(0.0, min(1000.0, pos)))
			except Exception:
				pass
			popup.after(250, _update_progress_loop)

		def stop_and_close() -> None:
			try:
				player.stop()
			except Exception:
				pass
			popup.destroy()

		play_btn = ttk.Button(controls, text="Play", command=play_now)
		play_btn.pack(side="right", padx=(0, 6))
		pause_btn = ttk.Button(controls, text="Pausar", command=toggle_pause)
		pause_btn.pack(side="right", padx=(0, 6))
		ttk.Button(controls, text="Reiniciar", command=restart_from_beginning).pack(side="right", padx=(0, 6))
		ttk.Button(controls, text="Abrir en navegador", command=lambda: webbrowser.open_new_tab(url)).pack(
			side="right", padx=8
		)
		ttk.Button(controls, text="Cerrar", command=stop_and_close).pack(side="right")

		progress_scale.configure(command=_on_seek_move)
		progress_scale.bind("<ButtonPress-1>", _on_seek_press)
		progress_scale.bind("<ButtonRelease-1>", _on_seek_release)

		popup.protocol("WM_DELETE_WINDOW", stop_and_close)
		popup.after(250, _update_progress_loop)
		threading.Thread(target=resolve_and_play, daemon=True).start()
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
			thumb_photo = ImageTk.PhotoImage(canvas)
			self.thumbnail_images[iid] = thumb_photo
			widgets["thumb"].configure(image=thumb_photo)
			widgets["thumb"].image = thumb_photo
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
		self.progress.stop()
		self.progress.configure(mode="determinate", maximum=100)
		self.progress["value"] = 0
		self.status_var.set("Descargando...")
		self._set_controls_state("disabled")
		self._log(f"Iniciando descarga de {len(urls)} video(s) en: {folder}")

		worker = threading.Thread(target=self._download_worker, args=(urls, folder), daemon=True)
		worker.start()

	def _download_worker(self, urls, folder: str) -> None:
		total = len(urls)
		current_index = {"value": 0}
		has_ffmpeg = self.ffmpeg_location is not None
		if not has_ffmpeg:
			self.output_queue.put(
				(
					"error",
					"No se encontro ffmpeg. Para salida MP4 compatible (audio AAC) instala ffmpeg o imageio-ffmpeg.",
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

		# Prioriza 1080p exacto y codecs compatibles con Windows (H264/AAC) en contenedor MP4.
		format_selector = (
			"bestvideo[height=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
			"bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
			"bestvideo[height=1080]+bestaudio/"
			"bestvideo[height<=1080]+bestaudio/"
			"best[height=1080][ext=mp4]/best[height<=1080][ext=mp4]"
		)

		base_options = {
			"format": format_selector,
			"outtmpl": os.path.join(folder, "%(title).200B.%(ext)s"),
			"noplaylist": True,
			"progress_hooks": [progress_hook],
			"quiet": True,
			"no_warnings": True,
			"retries": 5,
			"merge_output_format": "mp4",
			"ffmpeg_location": self.ffmpeg_location,
			"postprocessor_args": {
				"Merger": ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
			},
		}

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


if __name__ == "__main__":
	main()
