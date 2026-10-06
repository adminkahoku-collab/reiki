import io
import json
import os
import re
import time
import zipfile
from bs4 import BeautifulSoup
from google import genai
import streamlit as st

# ==========================================
# 1. HTML -> Markdown 変換ユーティリティ
# ==========================================


def parse_html_to_markdown(html_content: str) -> tuple[str, str]:
  """HTMLからタイトルと本文を抽出"""
  soup = BeautifulSoup(html_content, "html.parser")

  # タイトル抽出
  title = ""
  if soup.find("h1"):
    title = soup.find("h1").get_text(strip=True)
  elif soup.title:
    title = soup.title.get_text(strip=True)
  else:
    title = "無題の例規"
  title = re.sub(r"[\r\n\t]", "", title)

  # 本文抽出
  body_element = (
      soup.find("div", id="honbun") or soup.find("body") or soup
  )
  for tag in body_element(["script", "style", "nav", "header", "footer"]):
    tag.decompose()

  body_text = body_element.get_text()
  body_text = re.sub(r"\r\n|\r", "\n", body_text)
  body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

  return title, body_text


def convert_zip_to_md_dict(zip_file_bytes) -> dict[str, str]:
  """ZIP(DVDデータ)を解析し、必ず13編の個別Markdownデータ（辞書型）として分割抽出する"""
  md_dict = {}

  with zipfile.ZipFile(io.BytesIO(zip_file_bytes)) as z:
    hen_groups = {}

    for filename in z.namelist():
      if filename.endswith("/") or "__MACOSX" in filename:
        continue

      ext = os.path.splitext(filename)[1].lower()
      if ext in [".html", ".htm"]:
        parts = [p for p in filename.split("/") if p]

        # 第一階層のフォルダ名を編名として取得
        if len(parts) >= 2:
          hen_name = parts[0]
        else:
          hen_name = "第01編_未分類"

        if hen_name not in hen_groups:
          hen_groups[hen_name] = []
        hen_groups[hen_name].append(filename)

    # 13編それぞれに1つのMarkdownファイルを生成
    for hen_name, html_paths in sorted(hen_groups.items()):
      md_content = f"# {hen_name}\n\n"
      sorted_paths = sorted(html_paths)

      for path in sorted_paths:
        file_bytes = z.read(path)
        try:
          html_str = file_bytes.decode("cp932")
        except UnicodeDecodeError:
          html_str = file_bytes.decode("utf-8", errors="ignore")

        title, body = parse_html_to_markdown(html_str)
        md_content += f"## {title}\n\n{body}\n\n---\n\n"

      clean_hen_name = (
          f"{hen_name}.md" if not hen_name.endswith(".md") else hen_name
      )
      md_dict[clean_hen_name] = md_content

  return md_dict


# ==========================================
# 2. Gemini API 呼び出し関数
# ==========================================


def summarize_chunk_with_gemini(
    rules_chunk: list[dict], api_key: str
) -> dict[str, str]:
  """例規グループをまとめてGemini APIで要約"""
  if not api_key:
    return {}

  rules_text = ""
  for idx, r in enumerate(rules_chunk, 1):
    body_truncated = r["body"][:1500]
    rules_text += (
        f"--- 例規{idx} ---\nタイトル: {r['title']}\n本文:\n{body_truncated}\n\n"
    )

  prompt = f"""
あなたは自治体職員向けの例規要約アシスタントです。
以下の{len(rules_chunk)}件の例規について、それぞれの概要・要点を箇条書きで3行程度（100文字前後）で簡潔に要約してください。

【出力条件】
- 必ず指定されたJSONフォーマットのみを出力してください（説明文や ```json などの装飾タグは一切不要です）。
- キーは正確な「例規タイトル」、値は「箇条書きの要約テキスト」にしてください。

【出力フォーマット例】
{{
  "例規タイトル1": "・要点1\\n・要点2\\n・要点3",
  "例規タイトル2": "・要点1\\n・要点2"
}}

【対象例規データ】
{rules_text}
"""

  client = genai.Client(api_key=api_key)
  candidate_models = [
      "gemini-2.5-flash",
      "gemini-2.0-flash",
      "gemini-1.5-flash",
  ]

  for model_name in candidate_models:
    for attempt in range(3):
      try:
        response = client.models.generate_content(
            model=model_name, contents=prompt
        )

        res_text = response.text.strip()
        res_text = re.sub(r"^```json\s*", "", res_text)
        res_text = re.sub(r"^```\s*", "", res_text)
        res_text = re.sub(r"\s*```$", "", res_text)

        time.sleep(4.5)
        return json.loads(res_text)

      except Exception as e:
        err_str = str(e)
        if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
          time.sleep(8 * (attempt + 1))
          continue
        break

  return {}


# ==========================================
# 3. Streamlit メインUI
# ==========================================

st.set_page_config(
    page_title="例規集 自動変換＆要約システム",
    page_icon="📜",
    layout="wide",
)

st.title("📜 自治体例規集 自動変換＆要約システム")

# APIキーの取得
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")

with st.sidebar:
  st.header("⚙️ API設定")
  if not gemini_api_key:
    gemini_api_key = st.text_input(
        "Gemini API Key を入力してください", type="password"
    )

  st.divider()
  st.markdown("### 📌 処理手順")
  st.caption("1. DVDのZIPを1つアップロード")
  st.caption("2. 変換された13編のMarkdownを確認・ダウンロード")
  st.caption("3. 指定した編にAI要約を付与")

if not gemini_api_key:
  st.warning("👈 サイドバーから Gemini API Key を設定してください。")
  st.stop()

# セッション状態
if "md_dict" not in st.session_state:
  st.session_state.md_dict = {}
if "updated_md_dict" not in st.session_state:
  st.session_state.updated_md_dict = {}

# --- ステップ1: DVD(ZIP)のアップロードと13編Markdown化 ---
st.header("1. DVDデータ（ZIPファイル）のアップロード")

uploaded_zip = st.file_uploader(
    "DVDのZIPファイルをアップロードしてください", type=["zip"]
)

if uploaded_zip:
  if (
      "loaded_zip_name" not in st.session_state
      or st.session_state.loaded_zip_name != uploaded_zip.name
  ):
    with st.spinner("ZIP内の例規データを解析し、13編のMarkdownを生成中..."):
      st.session_state.md_dict = convert_zip_to_md_dict(uploaded_zip.getvalue())
      st.session_state.loaded_zip_name = uploaded_zip.name

  if st.session_state.md_dict:
    st.success(
        f"✅ 解析完了！ **{len(st.session_state.md_dict)} 件の編（Markdownファイル）**"
        " が作成されました。"
    )

    # 生成された13編の未要約Markdownの確認・ダウンロード機能
    st.markdown("#### 📥 生成されたMarkdownファイルの確認・ダウンロード")

    col_raw1, col_raw2 = st.columns(2)
    selected_raw_file = st.selectbox(
        "確認する編を選択:",
        list(st.session_state.md_dict.keys()),
        key="raw_select",
    )

    with col_raw1:
      if selected_raw_file:
        st.download_button(
            label=f"📄 {selected_raw_file} (未要約) をダウンロード",
            data=st.session_state.md_dict[selected_raw_file],
            file_name=selected_raw_file,
            mime="text/markdown",
        )

    with col_raw2:
      raw_zip_buffer = io.BytesIO()
      with zipfile.ZipFile(raw_zip_buffer, "w") as zf:
        for fname, fcontent in st.session_state.md_dict.items():
          zf.writestr(fname, fcontent)

      st.download_button(
          label="📦 全13編の未要約MarkdownをまとめてZIPダウンロード",
          data=raw_zip_buffer.getvalue(),
          file_name="unsummarized_13_hens.zip",
          mime="application/zip",
      )

    with st.expander("👁️️ 選択中の編のプレビュー表示（先頭1,500文字）"):
      st.text(st.session_state.md_dict[selected_raw_file][:1500] + "\n...")

# --- ステップ2: 対象の編を選択してAI要約を生成 ---
if st.session_state.md_dict:
  st.divider()
  st.header("2. 編を選択して要約の生成を開始")

  run_mode = st.radio(
      "実行モード:",
      [
          "選択した編のみ要約する（推奨: タイムアウト防止）",
          "全編を一括で要約する",
      ],
      horizontal=True,
  )

  selected_keys = []
  if "選択した編" in run_mode:
    selected_keys = st.multiselect(
        "要約を生成する編を選択してください（1編ずつを推奨）:",
        list(st.session_state.md_dict.keys()),
        default=list(st.session_state.md_dict.keys())[0:1],
    )
  else:
    selected_keys = list(st.session_state.md_dict.keys())

  if st.button("🤖 選択した編の要約生成を開始する", type="primary"):
    if not selected_keys:
      st.warning("処理対象の編が選択されていません。")
    else:
      progress_bar = st.progress(0)
      status_text = st.empty()

      total_files = len(selected_keys)

      for file_idx, file_name in enumerate(selected_keys):
        content = st.session_state.md_dict[file_name]

        rules_raw = content.split("## ")
        header = rules_raw[0]

        parsed_rules = []
        for block in rules_raw[1:]:
          lines = block.split("\n")
          title = lines[0].strip()
          body = "\n".join(lines[1:])
          parsed_rules.append({"title": title, "body": body})

        total_rules_in_file = len(parsed_rules)
        all_summaries = {}

        chunk_size = 10
        for i in range(0, total_rules_in_file, chunk_size):
          chunk = parsed_rules[i : i + chunk_size]
          end_idx = min(i + chunk_size, total_rules_in_file)

          status_text.info(
              f"📄 **[{file_idx+1}/{total_files}] {file_name}** を処理中..."
              f" ({i+1}〜{end_idx} / 全{total_rules_in_file}件)"
          )

          chunk_summaries = summarize_chunk_with_gemini(chunk, gemini_api_key)
          all_summaries.update(chunk_summaries)

        # 要約テキストを組み立てて組み込み
        updated_content = header
        for r in parsed_rules:
          summary_text = all_summaries.get(
              r["title"], "（要約生成エラー：一時的な混雑または出力不可）"
          )
          formatted_summary = "> " + summary_text.replace("\n", "\n> ")
          updated_content += (
              f"## {r['title']}\n\n> **【概要・要約】**\n{formatted_summary}\n\n{r['body']}"
          )

        st.session_state.updated_md_dict[file_name] = updated_content
        progress_bar.progress((file_idx + 1) / total_files)

      status_text.empty()
      st.success("🎉 選択した編の要約生成が完了しました！")

# --- ステップ3: 要約済みMarkdownのダウンロード ---
if st.session_state.updated_md_dict:
  st.divider()
  st.header("3. 要約付きMarkdownファイルのダウンロード")

  preview_file = st.selectbox(
      "要約完了ファイルを選択:",
      list(st.session_state.updated_md_dict.keys()),
  )

  col1, col2 = st.columns(2)

  with col1:
    if preview_file:
      st.download_button(
          label=f"📥 要約済み {preview_file} をダウンロード",
          data=st.session_state.updated_md_dict[preview_file],
          file_name=f"summary_{preview_file}",
          mime="text/markdown",
      )

  with col2:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
      for fname, fcontent in st.session_state.updated_md_dict.items():
        zf.writestr(f"summary_{fname}", fcontent)

    st.download_button(
        label="📦 要約完了ファイルをまとめてZIPダウンロード",
        data=zip_buffer.getvalue(),
        file_name="summarized_13_hens.zip",
        mime="application/zip",
    )

  if preview_file:
    with st.expander("👁️ 要約済みプレビュー（先頭2,000文字）"):
      st.text(st.session_state.updated_md_dict[preview_file][:2000] + "\n...")
