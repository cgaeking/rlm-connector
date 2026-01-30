"""Gradio Chat UI for the Knowledge Base."""

import logging
import subprocess
import sys
from pathlib import Path

import gradio as gr

from ..rlm_engine.engine import KnowledgeBaseEngine

logger = logging.getLogger(__name__)


def open_file_location(file_path: str) -> str:
    """Open the file location in the system file explorer.

    Args:
        file_path: Path to the file.

    Returns:
        Status message.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return f"Datei nicht gefunden: {file_path}"

        if sys.platform == "win32":
            # Windows: Explorer öffnen und Datei markieren
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            # macOS: Finder öffnen
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            # Linux: Ordner öffnen
            subprocess.run(["xdg-open", str(path.parent)], check=False)

        return f"Geöffnet: {path.parent}"
    except Exception as e:
        return f"Fehler: {str(e)}"


def create_chat_ui(
    engine: KnowledgeBaseEngine,
    db,
    sync_manager,
) -> gr.Blocks:
    """Create the Gradio chat interface.

    Args:
        engine: Knowledge base engine for queries.
        db: Document repository for status info.
        sync_manager: Sync manager for refresh operations.

    Returns:
        Gradio Blocks application.
    """

    # Store last sources for the "open file" feature
    last_sources: list[dict] = []

    async def chat(message: str, history: list) -> tuple[str, list[dict]]:
        """Handle chat messages."""
        nonlocal last_sources

        if not message.strip():
            return "Bitte stelle eine Frage.", []

        try:
            result = await engine.query(message)

            answer = result["answer"]

            # Add tool calls info (for transparency)
            tool_calls = result.get("tool_calls", [])
            if tool_calls:
                answer += "\n\n---\n🔧 **Verwendete Tools:**\n"
                for tc in tool_calls:
                    tool_name = tc["tool"]
                    if tool_name == "list_documents":
                        answer += "- 📋 Dokumentenliste abgerufen\n"
                    elif tool_name == "read_document":
                        answer += "- 📖 Dokument gelesen\n"
                    elif tool_name == "search_documents":
                        answer += f"- 🔍 Gesucht: '{tc['input'].get('query', '')}'\n"
                    elif tool_name == "search_fuzzy":
                        answer += f"- 🔎 Fuzzy-Suche: '{tc['input'].get('query', '')}'\n"
                    elif tool_name == "get_statistics":
                        answer += "- 📊 Statistiken abgerufen\n"

            # Store and display sources with full paths
            sources = result.get("sources", [])
            last_sources = sources

            if sources:
                answer += "\n\n📎 **Quellen:**\n"
                for i, source in enumerate(sources, 1):
                    file_name = source.get("file_name", "Unbekannt")
                    file_path = source.get("file_path", "")
                    answer += f"\n**[{i}] {file_name}**\n"
                    answer += f"   📁 `{file_path}`\n"

            # Add token usage
            tokens = result.get("tokens_used", 0)
            if tokens:
                answer += f"\n\n*({tokens} Tokens verwendet)*"

            return answer, sources

        except Exception as e:
            logger.error(f"Chat error: {e}")
            return f"Fehler bei der Verarbeitung: {str(e)}", []

    def get_status() -> dict:
        """Get current system status."""
        status = sync_manager.get_status()
        return {
            "Dokumente gesamt": status["total_documents"],
            "Indiziert": status["indexed_documents"],
            "Fehler": status["error_documents"],
            "Sync läuft": status["is_running"],
        }

    async def refresh_index():
        """Trigger index refresh."""
        if sync_manager.is_running:
            return "Sync läuft bereits..."

        try:
            await sync_manager.incremental_sync()
            return "Index erfolgreich aktualisiert!"
        except Exception as e:
            return f"Fehler: {str(e)}"

    def list_documents() -> str:
        """List recent documents."""
        docs = db.get_all_documents(status="indexed", limit=20)

        if not docs:
            return "Keine Dokumente indiziert."

        lines = ["**Letzte indizierte Dokumente:**\n"]
        for doc in docs:
            lines.append(f"- {doc.file_name} ({doc.file_type})")

        return "\n".join(lines)

    def format_sources_for_dropdown(sources: list[dict]) -> list[str]:
        """Format sources for dropdown selection."""
        if not sources:
            return []
        return [
            f"[{i+1}] {s.get('file_name', 'Unbekannt')}"
            for i, s in enumerate(sources)
        ]

    def open_selected_source(selection: str, sources_json: str) -> str:
        """Open the selected source file in file explorer."""
        import json

        if not selection or not sources_json:
            return "Keine Quelle ausgewählt"

        try:
            sources = json.loads(sources_json) if sources_json else []
            # Extract index from selection like "[1] filename.pdf"
            idx = int(selection.split("]")[0].replace("[", "")) - 1

            if 0 <= idx < len(sources):
                file_path = sources[idx].get("file_path", "")
                if file_path:
                    return open_file_location(file_path)
                return "Kein Dateipfad verfügbar"
            return "Ungültige Auswahl"
        except Exception as e:
            return f"Fehler: {str(e)}"

    # Build the UI
    with gr.Blocks(title="Knowledge Base Chat") as demo:
        # Hidden state for sources
        sources_state = gr.State([])

        gr.Markdown(
            """
            # 🔍 Unternehmens-Wissensdatenbank

            Stelle Fragen zu deinen Dokumenten. Der Assistent durchsucht den Index
            und beantwortet deine Fragen basierend auf den indizierten Inhalten.
            """
        )

        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    height=450,
                    label="Chat",
                )

                with gr.Row():
                    msg = gr.Textbox(
                        placeholder="z.B. 'Was steht in meinem Mietvertrag über Kündigungsfristen?'",
                        label="Deine Frage",
                        scale=4,
                        show_label=False,
                    )
                    submit = gr.Button("Fragen", variant="primary", scale=1)

                with gr.Row():
                    clear = gr.Button("Chat löschen", size="sm")
                    examples = gr.Examples(
                        examples=[
                            "Welche Dokumente habe ich?",
                            "Was sind die wichtigsten Verträge?",
                            "Finde alle Rechnungen",
                        ],
                        inputs=msg,
                    )

                # Source files section
                with gr.Accordion("📎 Quellen öffnen", open=False) as sources_accordion:
                    gr.Markdown("*Wähle eine Quelle aus der letzten Antwort zum Öffnen:*")
                    with gr.Row():
                        source_dropdown = gr.Dropdown(
                            choices=[],
                            label="Quelldatei",
                            interactive=True,
                            scale=3,
                        )
                        open_btn = gr.Button("📂 Im Explorer öffnen", scale=1)
                    open_result = gr.Textbox(
                        label="",
                        interactive=False,
                        show_label=False,
                        max_lines=1,
                    )
                    # Hidden field to store sources as JSON
                    sources_json = gr.Textbox(visible=False)

            with gr.Column(scale=1):
                gr.Markdown("### System-Status")

                status_display = gr.JSON(
                    value=get_status,
                    label="Status",
                )

                refresh_btn = gr.Button("🔄 Index aktualisieren", size="sm")
                refresh_output = gr.Textbox(
                    label="Refresh-Status",
                    interactive=False,
                    visible=True,
                )

                status_refresh = gr.Button("Status aktualisieren", size="sm")

                gr.Markdown("---")

                docs_display = gr.Markdown(
                    value=list_documents,
                    label="Dokumente",
                )

        # Event handlers
        async def respond(message, chat_history):
            import json

            if not message.strip():
                return "", chat_history, [], "", []

            bot_message, sources = await chat(message, chat_history)
            chat_history.append({"role": "user", "content": message})
            chat_history.append({"role": "assistant", "content": bot_message})

            # Update source dropdown
            dropdown_choices = format_sources_for_dropdown(sources)
            sources_json_str = json.dumps(sources) if sources else ""

            return "", chat_history, gr.update(choices=dropdown_choices, value=None), sources_json_str, sources

        msg.submit(
            respond,
            [msg, chatbot],
            [msg, chatbot, source_dropdown, sources_json, sources_state]
        )
        submit.click(
            respond,
            [msg, chatbot],
            [msg, chatbot, source_dropdown, sources_json, sources_state]
        )
        clear.click(
            lambda: ([], gr.update(choices=[], value=None), "", []),
            None,
            [chatbot, source_dropdown, sources_json, sources_state]
        )

        # Open file button
        open_btn.click(
            open_selected_source,
            [source_dropdown, sources_json],
            open_result
        )

        refresh_btn.click(refresh_index, outputs=refresh_output)
        status_refresh.click(get_status, outputs=status_display)

    return demo


def launch_ui(
    engine: KnowledgeBaseEngine,
    db,
    sync_manager,
    port: int = 7860,
    share: bool = False,
):
    """Launch the Gradio UI.

    Args:
        engine: Knowledge base engine.
        db: Document repository.
        sync_manager: Sync manager.
        port: Port to run on.
        share: Whether to create a public link.
    """
    demo = create_chat_ui(engine, db, sync_manager)
    demo.launch(
        server_port=port,
        share=share,
        show_error=True,
        theme=gr.themes.Soft(),
    )
