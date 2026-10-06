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
# 1. HTML -> Markdown 変換用ユーティリティ
# ==========================================


def parse_html_to_markdown(html_content: str) -> tuple[str, str]:
  """HTML文字列からタイトルと本文を抽出する"""
  soup = BeautifulSoup(html_content, "html.parser")

  # タイトルの抽出
  title = ""
  if soup.find("h1"):
    title = soup.find("h1").get_text(strip=True)
  elif soup.title:
    title = soup.title.get_text(strip=True)
  else:
    title = "無題の例規"
  title = re.sub(r"[\r\n\t]", "", title)

  # 本文の抽出
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
  """アップロードされたZIP(DVDデータ)を解析し、編ごとのMarkdownテキスト辞書を作成する"""
  md_dict = {}

  with zipfile.ZipFile(io.BytesIO(zip_file_bytes)) as z:
    # フォルダ（編）ごとにHTMLファイルを整理
    hen_groups = {}
    for filename in z.namelist():
      if filename.endswith("/") or filename.startswith("__MACOSX"):
        continue

      ext = os.path.splitext(filename)[1].lower()
      if ext in [".html", ".htm"]:
        parts = filename.split("/")
        # フォルダ構造から編名を取得（フォルダがない場合はルート）
        hen_name = (
            parts[0]
            if len(parts) > 1
            else "第01編"
            if len(parts) == 1
            else "その他"
        )

        if hen_name not in hen_groups:
          hen_groups[hen_name] = []
        hen_groups[hen_name].append(filename)

    # 各編ごとにMarkdownを生成
    for hen_name, html_paths in sorted(hen_groups.items()):
      md_content = f"# {hen_name}\n\n"
      sorted_paths = sorted(html_paths)

      for path in sorted_paths:
        file_bytes = z.read(path)
        # Shift_JIS(CP932)優先、ダメならUTF-8
        try:
          html_str = file_bytes.decode("cp932")
        except UnicodeDecodeError:
          html_str = file_bytes.decode("utf-8", errors="ignore")

        title, body = parse_html_to_markdown(html_str)
        md_content += f"## {title}\n\n{body}\n\n---\n\n"

      md_dict[f"{hen_name}.md"] = md_content

  return md_dict


# ==========================================
# 2. Gemini API 呼び出し関数（10件一括処理）
# ==========================================


def summarize_chunk_with_gemini(
    rules_chunk: list[dict], api_key: str
) -> dict[str, str]:
  """10件程度の例規グループをまとめてGemini APIに送信"""
  if not api_key:
    return {}

  rules_text = ""
  for idx, r in enumerate(rules_chunk, 1):
    body_truncated = r["body"][:1500]  # トークン節約
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
            model=model_name,
            contents=prompt,
        )

        res_text = response.text.strip()
        res_text = re.sub(r"^```json\s*", "", res_text)
        res_text = re.sub(r"^```\s*", "", res_text)
        res_text = re.sub(r"\s*```$", "", res_text)

        # 無料枠制限(15 RPM)対策のウェイト
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

st.title("📜 自治体例規集 全自動変換＆要約システム (Cloud完全対応版)")
st.markdown(
    "DVDデータ（ZIP形式）または作成済みのMarkdown（.md）をアップロードするだけで、**データの変換からGemini"
    " APIによる要約付与までブラウザ上で完結**します。"
)

# APIキーの取得（Secrets優先、なければサイドバー入力）
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")

with st.sidebar:
  st.header("⚙️ API設定")
  if not gemini_api_key:
    gemini_api_key = st.text_input(
        "Gemini API Key を入力してください", type="password"
    )

  st.divider()
  st.markdown("### 💡 運用ノウハウ")
  st.caption("クラウド版の通信切断（タイムアウト）を防ぐため、要約処理は")
  st.caption("**「1編（1ファイル）ずつ選択して処理」** を推奨します。")

if not gemini_api_key:
  st.warning(
      "👈 サイドバーから Gemini API Key を設定してください（または Streamlit"
      " Cloudの Secrets に登録）。"
  )
  st.stop()

# セッション状態管理
if "md_dict" not in st.session_state:
  st.session_state.md_dict = {}
if "updated_md_dict" not in st.session_state:
  st.session_state.updated_md_dict = {}

# --- ステップ1: データ取込・変換 ---
st.header("1. 例規データの読み込み（ZIP または MDファイル）")

upload_type = st.radio(
    "アップロードするデータ形式を選択してください:",
    ["DVDデータ（HTML群のZIPアーカイブ）", "作成済みMarkdownファイル（.md）"],
    horizontal=True,
)

if upload_type == "DVDデータ（HTML群のZIPアーカイブ）":
  uploaded_zip = st.file_uploader(
      "DVD内の各編フォルダをまとめたZIPファイルをアップロードしてください",
      type=["zip"],
  )
  if uploaded_zip:
    if st.button("🔨 ZIPを解析してMarkdownに変換する"):
      with st.spinner("ZIPファイル内のHTMLを解析・Markdown変換中..."):
        st.session_state.md_dict = convert_zip_to_md_dict(
            uploaded_zip.getvalue()
        )
      st.success(
          f"変換完了！ {len(st.session_state.md_dict)} 編のMarkdownデータを生成しました。"
      )

else:
  uploaded_mds = st.file_uploader(
      "13編のMarkdown（.md）ファイルをまとめてドラッグ＆ドロップしてください",
      type=["md"],
      accept_multiple_files=True,
  )
  if uploaded_mds:
    sorted_files = sorted(uploaded_mds, key=lambda x: x.name)
    for uploaded_file in sorted_files:
      string_data = uploaded_file.getvalue().decode("utf-8")
      st.session_state.md_dict[uploaded_file.name] = string_data
    st.success(
        f"合計 {len(st.session_state.md_dict)} 件のファイルを読み込みました。"
    )

# --- ステップ2: 要約生成の実行 ---
if st.session_state.md_dict:
  st.divider()
  st.header("2. 要約付与処理の実行")

  run_mode = st.radio(
      "処理モードを選択してください:",
      [
          "選択した編ファイルのみ処理する（推奨: タイムアウト防止）",
          "全ファイル（全編）を一括処理する",
      ],
      horizontal=True,
  )

  selected_keys = []
  if "選択した編" in run_mode:
    selected_keys = st.multiselect(
        "処理対象の編ファイルを選択してください:",
        list(st.session_state.md_dict.keys()),
        default=list(st.session_state.md_dict.keys())[0:1],
    )
  else:
    selected_keys = list(st.session_state.md_dict.keys())

  if st.button("🤖 要約生成を開始する", type="primary"):
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

      # 組み立て
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
    st.success("🎉 指定したファイルの要約付与処理が完了しました！")

# --- ステップ3: 結果確認 & ダウンロード ---
if st.session_state.updated_md_dict:
  st.divider()
  st.header("3. 処理結果の確認とダウンロード")

  preview_file = st.selectbox(
      "確認・ダウンロードするファイルを選択してください:",
      list(st.session_state.updated_md_dict.keys()),
  )

  col1, col2 = st.columns(2)

  with col1:
    if preview_file:
      st.download_button(
          label=f"📥 {preview_file} を単体ダウンロード",
          data=st.session_state.updated_md_dict[preview_file],
          file_name=f"summary_{preview_file}",
          mime="text/markdown",
      )

  with col2:
    # 処理完了済みのファイルをまとめてZIPダウンロード
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
      for fname, fcontent in st.session_state.updated_md_dict.items():
        zf.writestr(f"summary_{fname}", fcontent)

    st.download_button(
        label="📦 処理完了した全ファイルを一括ZIPダウンロード",
        data=zip_buffer.getvalue(),
        file_name="all_summarized_reiki.zip",
        mime="application/zip",
    )

  if preview_file:
    with st.expander("👁️ プレビューを表示（先頭2,000文字）"):
      st.text(st.session_state.updated_md_dict[preview_file][:2000] + "\n...")
