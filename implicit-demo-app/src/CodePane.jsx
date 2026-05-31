import CodeMirror from "@uiw/react-codemirror";
import { cpp } from "@codemirror/lang-cpp";
import { javascript } from "@codemirror/lang-javascript";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { Code2 } from "lucide-react";
import { useMemo } from "react";

const editorTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "#11151b",
    color: "#d8dee9",
    fontSize: "12.5px"
  },
  ".cm-scroller": {
    fontFamily: "\"JetBrains Mono\", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    lineHeight: "1.58"
  },
  ".cm-content": {
    padding: "12px 0 36px"
  },
  ".cm-line": {
    padding: "0 16px"
  },
  ".cm-gutters": {
    borderRight: "1px solid #2f3640",
    backgroundColor: "#1a1e24",
    color: "#647082"
  },
  ".cm-activeLine": {
    backgroundColor: "#18202a"
  },
  ".cm-activeLineGutter": {
    backgroundColor: "#202834",
    color: "#88c0d0"
  },
  ".cm-selectionBackground": {
    backgroundColor: "#355263 !important"
  },
  ".cm-cursor": {
    borderLeftColor: "#88c0d0"
  },
  ".cm-tooltip": {
    border: "1px solid #3b4252",
    backgroundColor: "#1a1e24"
  }
}, { dark: true });

const syntaxTheme = HighlightStyle.define([
  { tag: tags.keyword, color: "#81a1c1" },
  { tag: [tags.atom, tags.bool, tags.number], color: "#b48ead" },
  { tag: [tags.string, tags.special(tags.string)], color: "#a3be8c" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: "#6f7d91", fontStyle: "italic" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: "#88c0d0" },
  { tag: [tags.variableName, tags.propertyName], color: "#d8dee9" },
  { tag: [tags.definition(tags.variableName), tags.className], color: "#ebcb8b" },
  { tag: [tags.operator, tags.punctuation], color: "#8fbcbb" },
  { tag: tags.invalid, color: "#bf616a" }
]);

const basicSetup = {
  autocompletion: true,
  bracketMatching: true,
  closeBrackets: true,
  defaultKeymap: true,
  foldGutter: true,
  highlightActiveLine: true,
  highlightActiveLineGutter: true,
  highlightSelectionMatches: true,
  lineNumbers: true,
  searchKeymap: true
};

function languageExtension(language) {
  return language === "glsl"
    ? cpp()
    : javascript({ jsx: false, typescript: false });
}

export function CodePane({
  active = false,
  className = "",
  language,
  title,
  value,
  onChange
}) {
  const extensions = useMemo(() => [
    languageExtension(language),
    syntaxHighlighting(syntaxTheme),
    EditorView.lineWrapping
  ], [language]);

  return (
    <section className={`code-pane mobile-panel ${active ? "is-active" : ""} ${className}`}>
      <div className="section-bar">
        <div className="section-title-row">
          <Code2 size={14} />
          <span>{title}</span>
        </div>
        <span className="section-meta">{language}</span>
      </div>
      <div className="code-pane-body">
        <CodeMirror
          basicSetup={basicSetup}
          extensions={extensions}
          height="100%"
          theme={editorTheme}
          value={value}
          onChange={onChange}
        />
      </div>
    </section>
  );
}
