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
# 1. HTML -> Markdown 解析ユーティリティ
# ==========================================


def parse_reiki_html(html_content: str) -> tuple[str, str]:
  """例規本文HTML (H*****_J.html) からタイトルと本文を抽出"""
  soup = BeautifulSoup(html_content, "html.parser")

  # タイトル抽出
  title = ""
  title_tag = (
      soup.find("h1")
      or soup.find("div", class_="title")
      or soup.find("span", class_="title")
  )
  if title_tag:
    title = title_tag.get_text(strip=True)
  elif soup.title:
    title = soup.title.get_text(strip=True)
  else:
    title = "無題の例規"

  title = re.sub(r"[\r\n\t]", "", title)

  # 不要タグの削除
  for tag in soup(["script", "style", "nav", "header", "footer", "iframe"]):
    tag.decompose()

  # 本文抽出（honbun ID または body 全体）
  body_element = soup.find("div", id="honbun") or soup.find("body") or soup
  body_text = body_element.get_text()

  # テキスト整形
  body_text = re.sub(r"\r\n|\r", "\n", body_text)
  body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

  return title, body_text


def extract_13_hens_from_zip(zip_file_bytes) -> dict[str, str]:
  """bunya_00100000.html を起点にして、13編の構造通りに例規(H*****_J.html)を集約する"""
  md_dict = {}

  with zipfile.ZipFile(io.BytesIO(zip_file_bytes)) as z:
    file_list = z.namelist()

    # 1. 目次ファイル (bunya_00100000.html) のパスを検索
    bunya_path = None
    for f in file_list:
      if os.path.basename(f).lower() == "bunya_00100000.html":
        bunya_path = f
        break

    # フォールバック: 見つからない場合は bunya_ から始まる目次を検索
    if not bunya_path:
      for f in file_list:
        if "bunya_" in os.path.basename(f).lower() and f.endswith(".html"):
          bunya_path = f
          break

    if not bunya_path:
      st.error(
          "⚠️ 目次ファイル (bunya_00100000.html) がZIP内に見つかりませんでした。"
      )
      return {}

    # 2. 目次HTMLをデコードしてパース
    bunya_bytes = z.read(bunya_path)
    try:
      bunya_html = bunya_bytes.decode("cp932")
    except UnicodeDecodeError:
      bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

    soup_bunya = BeautifulSoup(bunya_html, "html.parser")

    # 目次内のリンク構造を解析（13編の定義を抽出）
    # 一般的な例規システムでは <ul> や <table> で第1編〜第13編がリンク定義されています
    hen_structure = (
        {}
    )  # { "第01編_総務": ["path/H1234_J.html", ...], ... }

    # リンクおよびリスト要素の解析
    current_hen_name = "第01編_未分類"
    bunya_dir = os.path.dirname(bunya_path)

    # リンク要素 (aタグ) を追跡
    a_tags = soup_bunya.find_all("a")

    for a in a_tags:
      text = a.get_text(strip=True)
      href = a.get("href", "")

      if not href:
        continue

      # 編のタイトルヘッダー等の判定（例: "第1編", "第01編", "第１編" 等）
      hen_match = re.search(r"(第\s*[0-9０-９1-13]{1,2}\s*編[^\s]*)", text)
      if hen_match:
        current_hen_name = hen_match.group(1)
        if current_hen_name not in hen_structure:
          hen_structure[current_hen_name] = []
        continue

      # 例規ファイル (H*****_J.html) へのリンク判定
      if re.search(r"H\d+.*\.html?", href, re.IGNORECASE):
        # 相対パスをZIP内のフルパスへ変換
        norm_path = os.path.normpath(os.path.join(bunya_dir, href)).replace(
            "\\", "/"
        )

        if current_hen_name not in hen_structure:
          hen_structure[current_hen_name] = []

        if norm_path not in hen_structure[current_hen_name]:
          hen_structure[current_hen_name].append(norm_path)

    # もし目次解析でリンクが十分に拾えなかった場合の補完処理（直接ZIP内の H*****_J.html を収集）
    if not hen_structure:
      st.warning(
          "目次からの自動リンク解析が困難だったため、全例規ファイル(H*****_J.html)から再構築します。"
      )
      all_reiki_files = [
          f
          for f in file_list
          if re.search(r"H\d+.*\.html?", os.path.basename(f), re.IGNORECASE)
      ]

      # 13等分して13編のMarkdownとして仮展開
      chunk_len = max(1, len(all_reiki_files) // 13)
      for i in range(13):
        h_name = f"第{i+1:02d}編"
        hen_structure[h_name] = all_reiki_files[
            i * chunk_len : (i + 1) * chunk_len
            if i < 12
            else len(all_reiki_files)
        ]

    # 3. 抽出した編・例規パス情報から13編のMarkdownを組み立て
    for idx, (hen_name, html_paths) in enumerate(
        sorted(hen_structure.items()), 1
    ):
      if not html_paths:
        continue

      formatted_hen_name = f"第{idx:02d}編_{hen_name.replace('第', '').replace('編', '')}"
      md_content = f"# {formatted_hen_name}\n\n"

      for path in html_paths:
        # ZIP内に該当ファイルが存在するか確認
        real_path = None
        for file_in_zip in file_list:
          if file_in_zip.lower() == path.lower() or os.path.basename(
              file_in_zip
          ).lower() == os.path.basename(path).lower():
            real_path = file_in_zip
            break

        if real_path:
          file_bytes = z.read(real_path)
          try:
            html_str = file_bytes.decode("cp932")
          except UnicodeDecodeError:
            html_str = file_bytes.decode("utf-8", errors="ignore")

          title, body = parse_reiki_html(html_str)
          md_content += f"## {title}\n\n{body}\n\n---\n\n"

      md_dict[f"{formatted_hen_name}.md"] = md_content

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
  st.caption("1. DVDのZIPをアップロード")
  st.caption("2. 目次(bunya_00100000.html)に基づき13編の.mdを出力")
  st.caption("3. 各編にAI要約を付与")

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
    with st.spinner(
        "bunya_00100000.html（目次）を解析し、13編の例規データ(H*****_J.html)を集約中..."
    ):
      st.session_state.md_dict = extract_13_hens_from_zip(
          uploaded_zip.getvalue()
      )
      st.session_state.loaded_zip_name = uploaded_zip.name

  if st.session_state.md_dict:
    st.success(
        f"✅ 目次構造のパースが完了しました！ **合計 {len(st.session_state.md_dict)} 件の編（Markdownファイル）**"
        " に正しく分割・作成されました。"
    )

    # 13編の分割結果一覧
    with st.expander("📋 作成された13編のMarkdownファイル一覧を確認"):
      for fname in sorted(st.session_state.md_dict.keys()):
        rule_count = st.session_state.md_dict[fname].count("\n## ")
        st.write(f"- **{fname}**（収録例規数: 約 {rule_count} 件）")

    # 未要約Markdownの確認・ダウンロード
    st.markdown("#### 📥 変換された13編のMarkdownファイルをダウンロード・確認")

    col_raw1, col_raw2 = st.columns(2)
    selected_raw_file = st.selectbox(
        "確認・ダウンロードする編を選択:",
        sorted(list(st.session_state.md_dict.keys())),
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

    with st.expander("👁 選択中の編のプレビュー表示（先頭1,500文字）"):
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
        sorted(list(st.session_state.md_dict.keys())),
        default=sorted(list(st.session_state.md_dict.keys()))[0:1],
    )
  else:
    selected_keys = sorted(list(st.session_state.md_dict.keys()))

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

        # 要約組み込み
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
      sorted(list(st.session_state.updated_md_dict.keys())),
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
        zf.writestr(fname, fcontent)

    st.download_button(
        label="📦 要約完了ファイルをまとめてZIPダウンロード",
        data=zip_buffer.getvalue(),
        file_name="summarized_13_hens.zip",
        mime="application/zip",
    )

  if preview_file:
    with st.expander("👁️ 要約済みプレビュー（先頭2,000文字）"):
      st.text(st.session_state.updated_md_dict[preview_file][:2000] + "\n...")
