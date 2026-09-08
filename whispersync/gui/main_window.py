"""Main application window for WhisperSync GUI."""

from __future__ import annotations

import dataclasses
import importlib.resources
import sys
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QMessageLogContext,
    QPropertyAnimation,
    QSettings,
    Qt,
    QThread,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from whispersync.config import WhisperSyncConfig
from whispersync.gui.widgets.drop_zone import DropZone
from whispersync.gui.widgets.help_page import HelpPage
from whispersync.gui.widgets.log_view import LogView
from whispersync.gui.widgets.settings_dialog import SettingsDialog
from whispersync.gui.widgets.timeline_preview import TimelinePreview
from whispersync.gui.worker import SyncWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WhisperSync — Audio/Video Synchronization")
        # Floor below which the layout would get cramped; the left column scrolls
        # rather than crushing its groups. Open larger so everything fits at once.
        self.setMinimumSize(1040, 640)
        self.resize(1280, 940)

        self.config = WhisperSyncConfig()
        self.settings = QSettings("WhisperSync", "WhisperSync")
        self._worker: SyncWorker | None = None
        self._thread: QThread | None = None
        self._output_user_set = False  # has the user picked an explicit output folder?
        # Set once the user has asked to close while a run is still going; the
        # actual close happens when the worker thread reports it has stopped.
        self._closing = False

        self._setup_ui()
        self._restore_state()

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 8, 16)
        left_layout.setSpacing(10)

        title = QLabel("WhisperSync")
        title.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        title.setStyleSheet("color: #E53935; margin-bottom: 0px;")
        left_layout.addWidget(title)

        subtitle = QLabel("Advanced Audio/Video Synchronization")
        subtitle.setStyleSheet("color: #9CA0A6; font-size: 12px; margin-bottom: 8px;")
        left_layout.addWidget(subtitle)

        video_group = QGroupBox("Video Folder")
        video_layout = QVBoxLayout(video_group)
        self.video_drop = DropZone(
            placeholder="Drop video folder here",
            accept_dirs=True,
            accepted_extensions=[],
        )
        self.video_drop.path_dropped.connect(self._on_video_dropped)
        video_layout.addWidget(self.video_drop)
        self.btn_browse_video = QPushButton("Browse...")
        self.btn_browse_video.clicked.connect(self._browse_video)
        video_layout.addWidget(self.btn_browse_video)
        left_layout.addWidget(video_group)

        audio_group = QGroupBox("Recorder Audio")
        audio_layout = QVBoxLayout(audio_group)
        self.audio_drop = DropZone(
            placeholder="Drop audio file(s) here",
            accept_dirs=False,
            accept_multiple=True,
            accepted_extensions=self.config.audio_exts,
        )
        self.audio_drop.path_dropped.connect(self._on_audio_dropped)
        self.audio_drop.paths_dropped.connect(self._on_audio_paths_dropped)
        audio_layout.addWidget(self.audio_drop)
        self.btn_browse_audio = QPushButton("Browse...")
        self.btn_browse_audio.clicked.connect(self._browse_audio)
        audio_layout.addWidget(self.btn_browse_audio)
        # Only meaningful with 2+ recorders: "best" keeps one audio lane per
        # clip (the strongest-matching recorder); "all" puts every recorder on
        # its own lane, for multi-mic/multi-speaker setups.
        self.recorder_mode_combo = QComboBox()
        self.recorder_mode_combo.addItems(["best", "all"])
        self.recorder_mode_combo.setCurrentText(self.config.recorder_mode)
        self.recorder_mode_combo.setEnabled(False)
        self.recorder_mode_combo.setToolTip(
            "best = one audio lane, strongest recorder per clip. "
            "all = every recorder on its own lane (multi-mic/multi-speaker)."
        )
        recorder_mode_row = QHBoxLayout()
        recorder_mode_row.addWidget(QLabel("Recorder mode:"))
        recorder_mode_row.addWidget(self.recorder_mode_combo, stretch=1)
        audio_layout.addLayout(recorder_mode_row)
        left_layout.addWidget(audio_group)

        output_group = QGroupBox("Output Folder")
        output_layout = QVBoxLayout(output_group)
        self.output_drop = DropZone(
            placeholder="Defaults to the video folder",
            accept_dirs=True,
            accepted_extensions=[],
        )
        self.output_drop.path_dropped.connect(self._on_output_dropped)
        output_layout.addWidget(self.output_drop)
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(self._browse_output)
        output_layout.addWidget(self.btn_browse_output)
        left_layout.addWidget(output_group)

        strategy_group = QGroupBox("Sync Strategy")
        strategy_layout = QVBoxLayout(strategy_group)
        self.radio1 = QRadioButton("1 — Global Linear Calibration")
        self.radio2 = QRadioButton("2 — Local Time-Stretch")
        self.radio3 = QRadioButton("3 — Hybrid (Global + Silence)  ·  recommended")
        strategy_radios = {1: self.radio1, 2: self.radio2, 3: self.radio3}
        # config.default_strategy is the single source of truth for the default
        # (the CLI's --strategy default reads the same field) — see
        # PROJECT_ANALYSIS.md §4.4. (The old strategy 3, "Silence Padding", was
        # merged into Hybrid — see §2.1.)
        strategy_radios[self.config.default_strategy].setChecked(True)
        for r in (self.radio1, self.radio2, self.radio3):
            r.setMinimumHeight(26)  # never let the label clip vertically
            r.toggled.connect(self._on_strategy_changed)
            strategy_layout.addWidget(r)
        left_layout.addWidget(strategy_group)

        options_group = QGroupBox("Options")
        options_layout = QFormLayout(options_group)
        self.timebase_combo = QComboBox()
        self.timebase_combo.addItems(["camera", "recorder"])
        options_layout.addRow("Timebase source:", self.timebase_combo)
        self.crossfade_check = QCheckBox("Crossfade segment seams (declick)")
        self.crossfade_check.setChecked(self.config.crossfade_enabled)
        options_layout.addRow(self.crossfade_check)

        # Boundary Flex — acoustic sub-frame refinement. config.boundary_flex is
        # the single source of truth for the default (see PROJECT_ANALYSIS.md
        # §4.4); on by default for the best lip-sync out of the box, costs a
        # little extra processing.
        self.flex_check = QCheckBox("Boundary Flex (acoustic sub-frame lip-sync)")
        self.flex_check.setChecked(self.config.boundary_flex)
        self.flex_check.setToolTip(
            "Fine-tune each phrase's position by cross-correlating the camera and "
            "recorder audio, so lips and sound match to within a frame."
        )
        options_layout.addRow(self.flex_check)

        # Pause ducking — attenuate inter-phrase pauses to hide ambience desync.
        self.duck_check = QCheckBox("Duck pauses (hide ambience desync)")
        self.duck_check.setChecked(self.config.pause_duck_enabled)
        self.duck_check.setToolTip(
            "Lower the volume during pauses between phrases so a slightly mis-synced "
            "room tone in the gaps is inaudible."
        )
        self.duck_check.toggled.connect(self._on_duck_toggled)
        options_layout.addRow(self.duck_check)

        # dB slider: 0 dB (off) … -60 dB (treated as silence). Shown only as enabled
        # when ducking is on. Step of 1 dB; label shows the live value (or −∞).
        self.duck_slider = QSlider(Qt.Orientation.Horizontal)
        self.duck_slider.setRange(-60, 0)  # -60 == full silence (−∞), 0 == no change
        self.duck_slider.setSingleStep(1)
        self.duck_slider.setPageStep(3)
        self.duck_slider.setValue(int(self.config.pause_duck_db))
        self.duck_slider.valueChanged.connect(self._on_duck_db_changed)
        self.duck_slider.setEnabled(self.duck_check.isChecked())
        self.duck_value = QLabel(self._duck_db_text(int(self.config.pause_duck_db)))
        duck_db_row = QHBoxLayout()
        duck_db_row.addWidget(self.duck_slider, stretch=1)
        duck_db_row.addWidget(self.duck_value)
        self.duck_db_label = QLabel("Pause level:")
        options_layout.addRow(self.duck_db_label, duck_db_row)
        self.duck_db_label.setEnabled(self.duck_check.isChecked())

        # Ambience track — strip the camera's own voice, keep the room tone, on its
        # own lane (needs the separate .sep-venv environment). Off by default.
        self.ambience_check = QCheckBox("Add camera-ambience track (no doubled voice)")
        self.ambience_check.setChecked(self.config.ambience_track)
        # Disabled with an explanatory tooltip when .sep-venv isn't set up,
        # instead of letting the user enable it and only finding out via a
        # warning at the end of a run. See PROJECT_ANALYSIS.md §7.4.
        from whispersync.engine import separation

        repo_root = Path(__file__).resolve().parents[2]
        if separation.is_available(repo_root):
            self.ambience_check.setToolTip(
                "Run AI source separation on the camera audio to remove its own (echoey) "
                "voice while keeping the ambience, on a separate lane."
            )
        else:
            # Uncheck as well as disable: with ambience now ON by default in
            # the config, a checked-but-disabled box would silently request a
            # feature that can't run (a warning every single run).
            self.ambience_check.setChecked(False)
            self.ambience_check.setEnabled(False)
            self.ambience_check.setToolTip(
                "Requires the '.sep-venv' environment, which is not set up. "
                "Run setup_sep_venv.sh to enable this feature."
            )
        options_layout.addRow(self.ambience_check)

        # Voice segmentation: split each rendered voice WAV into N-minute
        # pieces (cut in silence) so the NLE's own audio sync can re-align
        # every few minutes. 0 = one continuous file per clip (default).
        self.segment_combo = QComboBox()
        self.segment_combo.addItem("Monolith (one file)", 0)
        for minutes in (1, 2, 3, 5, 10):
            self.segment_combo.addItem(f"{minutes} min segments", minutes)
        idx = self.segment_combo.findData(self.config.voice_segment_minutes)
        self.segment_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.segment_combo.setToolTip(
            "Split the synced voice into segments of this length, cut at the "
            "quietest point near each boundary — useful when FCPX/Resolve "
            "re-synchronizes each audio item on import."
        )
        options_layout.addRow("Voice file split:", self.segment_combo)

        # Retake detection (off by default): find lines the speaker
        # re-recorded back-to-back and MARK each attempt on the timeline.
        # Markers rather than auditions: an audition switched the audio to the
        # keeper take without switching the picture, putting the voice seconds
        # ahead of the image. Nothing is cut, moved or re-timed.
        self.retakes_check = QCheckBox("Detect retakes (mark attempts on the timeline)")
        self.retakes_check.setChecked(self.config.detect_retakes)
        self.retakes_check.setToolTip(
            "Find lines re-recorded back-to-back (flub, stop, restart) and place a "
            "marker on each attempt, labelled with the one the speaker kept. "
            "Nothing is cut or re-timed. Off by default."
        )
        options_layout.addRow(self.retakes_check)

        # Self-check (off by default): re-transcribe each rendered voice
        # monolith and compare it against the camera clip's own transcript,
        # flagging content/timing spans --verify's acoustic lag measurement
        # can't see. "Warn only" just reports findings; "Warn + auto-repair"
        # additionally re-aligns and re-renders each flagged span's own small
        # stretch of audio before re-checking it once more. Costs one extra
        # Whisper pass per clip (two if any span needed a repair attempt).
        self.self_check_combo = QComboBox()
        self.self_check_combo.addItem("Off", "off")
        self.self_check_combo.addItem("Warn only", "warn")
        self.self_check_combo.addItem("Warn + auto-repair", "repair")
        idx = self.self_check_combo.findData(self.config.self_check_mode)
        self.self_check_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.self_check_combo.setToolTip(
            "Re-transcribe each rendered voice monolith and compare it against "
            "the camera clip's own transcript, flagging spans where content or "
            "timing diverge beyond normal Whisper jitter — catches defects the "
            "acoustic --verify check can't see. 'Warn only' just reports "
            "findings; 'Warn + auto-repair' additionally re-aligns and "
            "re-renders each flagged span before re-checking it. Off by "
            "default; costs one extra Whisper pass per clip."
        )
        options_layout.addRow("Self-check:", self.self_check_combo)

        # Voice enhancement (off by default): run a third-party model over the
        # rendered voice monolith, before self-check validates it. Six variants
        # were compared in a listening test; each trades speed/quality/license
        # differently (see the Help tab for the full pros/cons breakdown), so
        # this is a per-project choice rather than a single recommended default.
        # A mode whose ENVIRONMENT merely isn't set up stays selectable (it is
        # skipped with a warning, same UX as ambience_track on a missing
        # .sep-venv), but a mode with no backend in this build at all is
        # disabled outright: picking one used to cost a whole run before
        # "not available yet in this build" appeared as the last log line.
        self.voice_enhance_combo = QComboBox()
        self.voice_enhance_combo.addItem("Off", "off")
        self.voice_enhance_combo.addItem("Denoise (fast, safest)", "denoise")
        self.voice_enhance_combo.addItem("Denoise + De-reverb", "denoise_dereverb")
        self.voice_enhance_combo.addItem(
            "Resemble Enhance (studio timbre, experimental)", "resemble"
        )
        self.voice_enhance_combo.addItem("SGMSE+ Denoise (slow, cleanest)", "sgmse_denoise")
        self.voice_enhance_combo.addItem("SGMSE+ De-reverb (slow)", "sgmse_dereverb")
        self.voice_enhance_combo.addItem("RE-USE (fastest, noncommercial license only)", "reuse")
        self._disable_unimplemented_enhance_modes()
        # A saved config can still name a greyed-out mode; don't restore it
        # into the combo, or the run would set off with a mode that can't run.
        from whispersync.engine.enhance import IMPLEMENTED_MODES as _IMPL

        idx = (
            self.voice_enhance_combo.findData(self.config.voice_enhance)
            if self.config.voice_enhance in _IMPL
            else -1
        )
        self.voice_enhance_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.voice_enhance_combo.setToolTip(
            "Run a third-party model over the rendered voice monolith before "
            "self-check. 'Denoise'/'Denoise + De-reverb' reuse the .sep-venv "
            "stack already used for ambience — fast and safe. 'Resemble "
            "Enhance' gives a studio-like timbre but is experimental (some "
            "upstream patches are undocumented). The 'SGMSE+' modes sound "
            "cleanest but run ~5x slower than realtime (diffusion) — needs a "
            "separate '.enh-venv'. 'RE-USE' is the fastest all-in-one option "
            "but its model is NSCLv1 (noncommercial use only) and needs "
            "Docker plus NVIDIA's own RE-USE source under their license — no "
            "backend for it in this build yet, so it is greyed out. Off "
            "by default; a mode whose environment isn't set up is skipped "
            "with a warning, keeping the unenhanced audio."
        )
        options_layout.addRow("Voice enhancement:", self.voice_enhance_combo)

        left_layout.addWidget(options_group)

        self.btn_settings = QPushButton("Transcription Settings...")
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        left_layout.addWidget(self.btn_settings)

        self.btn_sync = QPushButton("SYNC")
        self.btn_sync.setObjectName("primaryButton")
        self.btn_sync.setMinimumHeight(48)
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.btn_sync.clicked.connect(self._start_sync)
        left_layout.addWidget(self.btn_sync)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_sync)
        left_layout.addWidget(self.btn_cancel)

        left_layout.addStretch()

        right_tabs = QTabWidget()

        run_tab = QWidget()
        right_layout = QVBoxLayout(run_tab)
        right_layout.setContentsMargins(8, 12, 8, 8)
        right_layout.setSpacing(12)

        timeline_group = QGroupBox("Timeline")
        timeline_layout = QVBoxLayout(timeline_group)
        self.timeline_preview = TimelinePreview()
        self.timeline_preview.setMinimumHeight(180)
        timeline_layout.addWidget(self.timeline_preview)
        right_layout.addWidget(timeline_group, stretch=1)

        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.stage_label = QLabel("Ready")
        self.stage_label.setStyleSheet("color: #9CA0A6; font-size: 13px;")
        progress_layout.addWidget(self.stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        # Smoothly tween the bar to each new value instead of snapping — small touch
        # that makes progress feel continuous rather than steppy.
        self._progress_anim = QPropertyAnimation(self.progress_bar, b"value")
        self._progress_anim.setDuration(220)
        self._progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        right_layout.addWidget(progress_group)

        result_group = QGroupBox("Results")
        result_layout = QVBoxLayout(result_group)
        self.result_label = QLabel("No results yet")
        self.result_label.setStyleSheet("color: #9CA0A6;")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # A word-wrapped label reports a collapsed height hint; reserve room for
        # the four metric lines (the output path may wrap onto a fifth) so the
        # readout never clips.
        self.result_label.setMinimumHeight(96)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Monospace metrics so the K / offset / residual figures line up.
        result_font = QFont()
        result_font.setFamilies(["JetBrains Mono", "DejaVu Sans Mono", "Consolas", "monospace"])
        result_font.setStyleHint(QFont.StyleHint.Monospace)
        result_font.setPointSize(10)
        self.result_label.setFont(result_font)
        result_layout.addWidget(self.result_label)
        self.btn_open_folder = QPushButton("Open Output Folder")
        self.btn_open_folder.setEnabled(False)
        self.btn_open_folder.clicked.connect(self._open_output_folder)
        result_layout.addWidget(self.btn_open_folder)
        # Re-running only changes strategy_id; transcripts are cached
        # (config.use_cache), so a re-run skips straight to alignment/render
        # instead of re-transcribing every recorder/clip from scratch.
        self.btn_rerun = QPushButton("Re-run with Selected Strategy")
        self.btn_rerun.setEnabled(False)
        self.btn_rerun.setToolTip(
            "Re-run using the currently selected strategy above. Transcripts are "
            "cached, so this skips straight to alignment/render."
        )
        self.btn_rerun.clicked.connect(self._start_sync)
        result_layout.addWidget(self.btn_rerun)
        right_layout.addWidget(result_group)

        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.log_view = LogView()
        log_layout.addWidget(self.log_view)
        right_layout.addWidget(log_group, stretch=1)

        right_tabs.addTab(run_tab, "Run")

        self.help_page = HelpPage()
        right_tabs.addTab(self.help_page, "Help")
        self.right_tabs = right_tabs

        # Wrap the controls column in a scroll area so a short window scrolls it
        # instead of crushing the groups (the radios used to clip). The panel keeps
        # its natural width and never shrinks below what the content needs.
        left_panel.setMinimumWidth(320)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_panel)
        left_scroll.setMinimumWidth(340)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 0)  # controls column stays compact
        splitter.setStretchFactor(1, 1)  # timeline / simulator side absorbs resize
        splitter.setSizes([400, 760])

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Affordance: every clickable control gets the hand cursor.
        for btn in self.findChildren(QPushButton):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        for rb in self.findChildren(QRadioButton):
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        for cb in self.findChildren(QCheckBox):
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.duck_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        self._on_strategy_changed()

    def _disable_unimplemented_enhance_modes(self) -> None:
        """Grey out voice-enhancement modes that have no backend in this build.

        A mode whose environment just isn't installed stays selectable — the
        run warns and keeps the unenhanced audio, and the user may well be
        about to install it. A mode with no backend at all can never work, and
        selecting one silently cost a full multi-hour run before the pipeline
        said so at the end."""
        from whispersync.engine import enhance

        model = self.voice_enhance_combo.model()
        for i in range(self.voice_enhance_combo.count()):
            mode = self.voice_enhance_combo.itemData(i)
            if mode in enhance.IMPLEMENTED_MODES:
                continue
            self.voice_enhance_combo.setItemText(
                i, f"{self.voice_enhance_combo.itemText(i)} — not in this build"
            )
            item = model.item(i) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(False)

    def _get_strategy_id(self) -> int:
        if self.radio2.isChecked():
            return 2
        if self.radio3.isChecked():
            return 3
        return 1

    def _on_strategy_changed(self) -> None:
        self.help_page.set_strategy(self._get_strategy_id())

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.config = dialog.apply_to(self.config)
            self.log_view.append_log(
                f"Transcription settings updated: model={self.config.model}, "
                f"device={self.config.device}, mode={self.config.transcribe_mode}"
            )

    @staticmethod
    def _duck_db_text(db: int) -> str:
        # The slider floor is treated as full silence.
        return "−∞ dB" if db <= -60 else f"{db:+d} dB" if db != 0 else "0 dB (off)"

    def _on_duck_toggled(self, on: bool) -> None:
        self.duck_slider.setEnabled(on)
        self.duck_db_label.setEnabled(on)
        self.duck_value.setEnabled(on)

    def _on_duck_db_changed(self, value: int) -> None:
        self.duck_value.setText(self._duck_db_text(value))

    # Source selection goes through ONE handler per kind, whichever way it was
    # chosen. Browsing for a video folder used to skip the persistence the
    # drop-zone did, and only the FIRST recorder was ever saved — so a restart
    # could restore last week's folder, or silently start a multi-recorder run
    # with one file. What is persisted is also what is restored.

    def _on_video_dropped(self, path: str) -> None:
        self.settings.setValue("last_video_dir", path)
        self.log_view.append_log(f"Video folder: {path}")
        self._maybe_default_output(path)

    def _on_audio_dropped(self, path: str) -> None:
        self.settings.setValue("last_audio_file", path)
        self.log_view.append_log(f"Audio file: {path}")

    def _on_audio_paths_dropped(self, paths: list) -> None:
        self.recorder_mode_combo.setEnabled(len(paths) > 1)
        # The whole set, not just the first: a two-recorder project restored as
        # one recorder runs happily and produces a different, wrong result.
        self.settings.setValue("last_audio_files", [str(p) for p in paths])
        if paths:
            self.settings.setValue("last_audio_file", str(paths[0]))
        if len(paths) > 1:
            self.log_view.append_log(f"{len(paths)} recorder audio files selected")

    def _on_output_dropped(self, path: str) -> None:
        self._output_user_set = True
        self.log_view.append_log(f"Output folder: {path}")

    def _maybe_default_output(self, video_path: str) -> None:
        """Until the user picks one explicitly, the output folder follows the
        sources — they usually live on a volume with room to spare."""
        if video_path and not self._output_user_set:
            self.output_drop.set_path(video_path)

    def _browse_video(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Video Folder", str(self.settings.value("last_video_dir", "") or "")
        )
        if path:
            self.video_drop.set_path(path)
            self._on_video_dropped(path)

    def _browse_audio(self) -> None:
        exts = " ".join(f"*{e}" for e in self.config.audio_exts)
        last = str(self.settings.value("last_audio_file", "") or "")
        start_dir = str(Path(last).parent) if last else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Audio File(s)", start_dir, f"Audio Files ({exts})"
        )
        if paths:
            self.audio_drop.set_paths(paths)
            self._on_audio_paths_dropped(paths)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self.output_drop.set_path(path)
            self._output_user_set = True

    def _start_sync(self) -> None:
        video_path = self.video_drop.current_path
        audio_paths = self.audio_drop.current_paths

        if not video_path or not Path(video_path).is_dir():
            QMessageBox.warning(self, "Error", "Please select a video folder.")
            return
        if not audio_paths or not all(Path(p).is_file() for p in audio_paths):
            QMessageBox.warning(self, "Error", "Please select at least one audio file.")
            return

        # Output goes to the chosen folder, or next to the sources by default.
        output_dir = Path(self.output_drop.current_path or video_path)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Error", f"Cannot use output folder:\n{exc}")
            return
        output_path = output_dir / "sync_output.fcpxml"

        strategy_id = self._get_strategy_id()
        # A copy, not self.config mutated in place: self.config is also read by
        # the UI (e.g. re-opening file dialogs), and the pipeline runs on a
        # background thread — if the user toggles a checkbox while a run is in
        # flight, mutating the shared object would change settings out from
        # under the running pipeline mid-run. See PROJECT_ANALYSIS.md §4.5.
        db = self.duck_slider.value()
        # Slider floor (-60) means full silence; map it to a very negative dB so
        # the ducking filter zeroes the gain (duck_filter_chain treats <= -120
        # as 0).
        run_config = dataclasses.replace(
            self.config,
            timebase_source=self.timebase_combo.currentText(),
            crossfade_enabled=self.crossfade_check.isChecked(),
            boundary_flex=self.flex_check.isChecked(),
            pause_duck_enabled=self.duck_check.isChecked(),
            pause_duck_db=-200.0 if db <= -60 else float(db),
            ambience_track=self.ambience_check.isChecked(),
            recorder_mode=self.recorder_mode_combo.currentText(),
            voice_segment_minutes=int(self.segment_combo.currentData() or 0),
            detect_retakes=self.retakes_check.isChecked(),
            self_check_mode=str(self.self_check_combo.currentData() or "off"),
            voice_enhance=str(self.voice_enhance_combo.currentData() or "off"),
        )

        self.right_tabs.setCurrentIndex(0)  # show the Run tab during processing
        self.btn_sync.setEnabled(False)
        self.btn_rerun.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.log_view.clear_log()
        self.log_view.append_log(f"Starting sync with Strategy {strategy_id}...")

        self._worker = SyncWorker(
            config=run_config,
            video_dir=Path(video_path),
            audio_files=[Path(p) for p in audio_paths],
            strategy_id=strategy_id,
            output_path=output_path,
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.stage.connect(self._on_stage)
        self._worker.log.connect(lambda msg: self.log_view.append_log(msg))
        self._worker.timeline.connect(self.timeline_preview.set_tracks)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        # All THREE terminal outcomes stop the thread. Cancellation used to be
        # reported only as a log line, so the thread kept running and the
        # buttons stayed disabled — the window looked busy forever.
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        # Deferred close (see closeEvent) waits for this, and a re-run needs
        # the previous objects gone before new ones are wired up.
        self._thread.finished.connect(self._on_thread_finished)

        self._thread.start()

    def _cancel_sync(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.stage_label.setText("Cancelling…")
            self.log_view.append_log(
                "Cancellation requested — finishing the current step...", "WARNING"
            )

    def _on_progress(self, value: int) -> None:
        # Animate toward the new value (skip the tween for resets to 0).
        if value <= 0:
            self._progress_anim.stop()
            self.progress_bar.setValue(0)
            return
        self._progress_anim.stop()
        self._progress_anim.setStartValue(self.progress_bar.value())
        self._progress_anim.setEndValue(value)
        self._progress_anim.start()

    def _on_stage(self, stage: str) -> None:
        self.stage_label.setText(stage)
        self.status_bar.showMessage(stage)

    def _on_finished(self, result: object) -> None:
        self._restore_idle_ui()
        self._on_progress(100)
        self.stage_label.setText("Done!")

        from whispersync.models import SyncResult

        if isinstance(result, SyncResult):
            self.result_label.setStyleSheet("color: #F0F0F1;")
            self.result_label.setText(
                f"{'Anchors':<9}{result.anchors_used}\n"
                f"{'K':<9}{result.alignment.k:.6f}\n"
                f"{'Residual':<9}{result.alignment.residual_ms:.1f} ms\n"
                f"{'Output':<9}{result.fcpxml_path}"
            )
            self.btn_open_folder.setEnabled(True)
            self.btn_rerun.setEnabled(True)
            self._output_path = result.fcpxml_path.parent
            # The timeline is kept live via the worker's `timeline` signal.

            # Pipeline warnings (unaligned clips, high residual, strategy
            # advice, FCPXML validation failures, ...) previously only reached
            # the user via the CLI printout — the GUI silently dropped them.
            # See PROJECT_ANALYSIS.md §7.6.
            for warning in result.warnings:
                self.log_view.append_log(warning, "WARNING")

        self.log_view.append_log("Sync complete!", "INFO")
        self.status_bar.showMessage("Sync complete!")

    def _on_cancelled(self) -> None:
        """Restore the window after a cancelled run — the same job `_on_finished`
        and `_on_error` do for the other two outcomes."""
        self._restore_idle_ui()
        self.stage_label.setText("Cancelled")
        self._on_progress(0)
        self.log_view.append_log("Sync cancelled.", "WARNING")
        self.status_bar.showMessage("Cancelled")

    def _on_thread_finished(self) -> None:
        """The worker thread has actually stopped.

        Anything that must not happen while a pipeline is still running — a
        deferred window close, dropping references to the worker — belongs
        here, not at the moment cancellation was merely *requested*.
        """
        self._restore_idle_ui()
        if self._closing:
            self.close()

    def _restore_idle_ui(self) -> None:
        self.btn_sync.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_error(self, msg: str) -> None:
        self._restore_idle_ui()
        self.stage_label.setText("Error!")
        self.log_view.append_log(f"ERROR: {msg}", "ERROR")
        self.status_bar.showMessage("Error!")
        QMessageBox.critical(self, "Sync Error", msg)

    def _open_output_folder(self) -> None:
        # QDesktopServices.openUrl is the cross-platform way to reveal a folder
        # (xdg-open only exists on Linux; Windows/macOS need explorer/open) — see
        # PROJECT_ANALYSIS.md §3.1.
        if hasattr(self, "_output_path"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_path)))

    def _restore_state(self) -> None:
        """Re-select the previous session's sources, and SAY what is missing.

        A restored selection that silently dropped the files that no longer
        exist (an unmounted card, a moved folder) would start a run over an
        incomplete set of recorders and produce a quietly different result.
        Anything that cannot be restored is named in the log and left
        unselected, so the user chooses rather than the app guessing.
        """
        last_video = str(self.settings.value("last_video_dir", "") or "")
        if last_video:
            if Path(last_video).exists():
                self.video_drop.set_path(last_video)
                self._maybe_default_output(last_video)
            else:
                self.log_view.append_log(
                    f"Previous video folder is no longer available: {last_video}", "WARNING"
                )

        stored = self.settings.value("last_audio_files", None)
        if isinstance(stored, str):
            stored = [stored]
        if not stored:
            single = str(self.settings.value("last_audio_file", "") or "")
            stored = [single] if single else []

        present = [str(p) for p in stored if Path(str(p)).exists()]
        missing = [str(p) for p in stored if not Path(str(p)).exists()]
        if missing:
            self.log_view.append_log(
                f"{len(missing)} previously selected recorder file(s) are no longer "
                f"available: {', '.join(missing[:3])}"
                f"{', …' if len(missing) > 3 else ''}",
                "WARNING",
            )
        if present and not missing:
            self.audio_drop.set_paths(present)
            self.recorder_mode_combo.setEnabled(len(present) > 1)

    def closeEvent(self, event: object) -> None:
        """Close only once the pipeline has really stopped.

        `quit()` asks a thread's EVENT LOOP to exit; it does nothing to a slot
        that is still executing, which is exactly where `run_pipeline` spends
        the entire run. So the old code asked politely, ignored the result of
        `wait(3000)`, and closed anyway — destroying a QThread with a live
        worker and abandoning half-written outputs.

        Instead: request cancellation, refuse this close, and close for real
        from `_on_thread_finished`. The window stays responsive meanwhile
        (blocking on `wait()` would freeze it), the user sees that shutdown is
        in progress, and a second close press force-quits if they would rather
        not wait.
        """
        if self._thread and self._thread.isRunning():
            if self._closing:
                # Second attempt: the user has chosen not to wait.
                self.log_view.append_log(
                    "Closing without waiting for the pipeline to stop.", "WARNING"
                )
                self._thread.quit()
                self._thread.wait(2000)
                super().closeEvent(event)  # type: ignore[arg-type]
                return
            self._closing = True
            if self._worker:
                self._worker.cancel()
            self.log_view.append_log(
                "Stopping the pipeline before closing… (close again to force)", "WARNING"
            )
            self.stage_label.setText("Stopping…")
            event.ignore()  # type: ignore[attr-defined]
            return
        super().closeEvent(event)  # type: ignore[arg-type]


def _install_quiet_message_handler() -> None:
    """Drop one benign Qt warning, pass everything else through unchanged.

    On headless / portal-less GNOME, Qt probes ``org.freedesktop.portal.Settings``
    for the system theme; when that portal isn't running it prints
    "Call to org.freedesktop.portal.Settings.ReadAll failed …". It's emitted by an
    unconditional ``qWarning`` (not a logging category), so ``QT_LOGGING_RULES``
    can't mute it — a message handler is the only hook. We ship our own theme, so
    the missing portal changes nothing.
    """

    def handler(mode: QtMsgType, context: QMessageLogContext, message: str | None) -> None:
        if message and "org.freedesktop.portal" in message:
            return
        if message:
            print(message, file=sys.stderr)

    qInstallMessageHandler(handler)


def load_stylesheet() -> str:
    """The GUI stylesheet, read as a PACKAGE RESOURCE.

    ``Path(__file__).parent / "theme.qss"`` assumes the package is a directory
    of real files. That holds in a checkout and inside a PyInstaller bundle,
    but a wheel that does not DECLARE the file simply will not contain it — and
    the old code, finding no file, silently skipped the stylesheet, so an
    installed WhisperSync started unstyled with nothing logged. Reading it
    through ``importlib.resources`` works for every packaging form, and a
    missing resource is reported instead of ignored.
    """
    try:
        return importlib.resources.files("whispersync.gui").joinpath("theme.qss").read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError) as e:
        # Not fatal — the app is usable unstyled — but never silent.
        print(f"warning: GUI stylesheet could not be loaded ({e})", file=sys.stderr)
        return ""


def main() -> None:
    _install_quiet_message_handler()

    app = QApplication(sys.argv)

    # Modern UI font stack with explicit anti-aliasing; falls back gracefully
    # to whatever the platform provides.
    app_font = QFont()
    app_font.setFamilies(
        ["Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Arial", "sans-serif"]
    )
    app_font.setPointSize(10)
    app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(app_font)

    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
