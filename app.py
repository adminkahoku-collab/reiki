import io
import os
import re
import time
import zipfile
import google.generativeai as genai
import streamlit as st

# --- ページ設定 ---
st.set_page_config(
    page_title="例規ナレッジ化処理ツール", layout="wide"
)

# --- 1. パスワード認証機能 ---
PASSWORD = "reiki063215"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 例規データ変換システム")
    input_pwd = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        if input_pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません。")
    st.stop()


# --- 2. メイン画面 ---
st.title("📄 例規データ ナレッジ化処理システム")
st.caption("第1段階: Markdown変換 & ボリューム確認 ➔ 第2段階: 無償API要約付与")

# サイドバー設定
st.sidebar.header("設定")
api_key = st.sidebar.text_input(
    "Gemini API Key (Free Tier)", type="password"
)

# セッション状態の初期化
if "converted_files" not in st.session_state:
    st.session_state.converted_files = {}


# --- 第1段階：Markdown化とボリューム把握 ---
st.header("【第1段階】Markdownファイルの読み込みとボリューム確認")

uploaded_files = st.file_uploader(
    "1段階目の標準Markdownファイル（複数可）を選択してください",
    type=["md"],
    accept_multiple_files=True,
)

if uploaded_files:
    total_rules_count = 0
    file_rule_mapping = {}

    # ボリュームの解析
    for uploaded_file in uploaded_files:
        content = uploaded_file.read().decode("utf-8")
        uploaded_file.seek(0)  # ポインタを戻す

        # '## ' の見出し数をカウント（条例数）
        rules = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
        rule_count = len(rules)

        file_rule_mapping[uploaded_file.name] = {
            "content": content,
            "rule_count": rule_count,
        }
        total_rules_count += rule_count

    st.session_state.converted_files = file_rule_mapping

    # ボリューム情報の表示
    col1, col2, col3 = st.columns(3)
    col1.metric("総ファイル数", f"{len(uploaded_files)} 件")
    col2.metric("総条例（##）数", f"{total_rules_count} 件")

    # 無償API (15 RPM -> 1件約4.5秒待機) の場合の所要時間計算
    estimated_seconds = total_rules_count * 4.5
    est_min = int(estimated_seconds // 60)
    est_sec = int(estimated_seconds % 60)
    col3.metric("第2段階の予想処理時間", f"約 {est_min}分 {est_sec}秒")

    st.info(
        f"💡 ボリューム確認完了: 合計 **{total_rules_count} 件** の条例が見つかりました。第2段階の処理を開始できます。"
    )

    st.divider()

    # --- 第2段階：無償APIによる要約付与処理 ---
    st.header("【第2段階】要約・タグの自動付与（Gemini API）")

    if not api_key:
        st.warning(
            "⚠️ 第2段階を進めるには、サイドバーに Gemini API Key を入力してください。"
        )
    else:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-3.6-flash")

        if st.button("🚀 要約の自動付与を開始する"):
            progress_bar = st.progress(0)
            status_area = st.empty()
            time_area = st.empty()

            start_time = time.time()
            processed_rules_count = 0
            final_files = {}

            # 要約生成関数
            def get_summary(title, text_content):
                prompt = f"""
あなたは自治体例規の整理補助AIです。
以下の例規の「タイトル」と「本文」を読み、概要とカテゴリを以下のフォーマットで短く出力してください。

【対象例規】
タイトル: {title}
本文冒頭: {text_content[:1000]}

【出力フォーマット】
概要：[1〜2文で何について定めたものか] / 対象カテゴリ：[関連する検索単語や分野をカンマ区切りで3〜5個]
"""
                try:
                    res = model.generate_content(prompt)
                    time.sleep(4.0)  # 無償枠のレート制限対策（1分間15回まで）
                    return res.text.strip().replace("\n", " ")
                except Exception as e:
                    time.sleep(8.0)
                    return "概要の自動生成に失敗しました"

            # 処理ループ
            for file_name, file_data in st.session_state.converted_files.items(
                
            ):
                text = file_data["content"]
                sections = re.split(r"\n(?=##\s+)", text)
                new_sections = []

                for section in sections:
                    match = re.search(r"^##\s+(.+)$", section, re.MULTILINE)
                    if match:
                        rule_title = match.group(1).strip()
                        processed_rules_count += 1

                        # 時間計算
                        elapsed = time.time() - start_time
                        avg_time_per_item = (
                            elapsed / processed_rules_count
                            if processed_rules_count > 0
                            else 4.5
                        )
                        remaining_items = (
                            total_rules_count - processed_rules_count
                        )
                        remaining_seconds = remaining_items * avg_time_per_item

                        rem_min = int(remaining_seconds // 60)
                        rem_sec = int(remaining_seconds % 60)

                        # 画面表示のリアルタイム更新
                        status_area.markdown(
                            f"**処理中 ({processed_rules_count}/{total_rules_count} 件):** `{file_name}` ➔ `## {rule_title}`"
                        )
                        time_area.markdown(
                            f"⏱ 経過時間: **{int(elapsed)}秒** | 🏁 残り予想時間: **約 {rem_min}分 {rem_sec}秒**"
                        )

                        # API呼び出し
                        summary = get_summary(rule_title, section)
                        summary_tag = f"<!-- summary: {summary} -->\n"
                        section = summary_tag + section

                        # プログレスバー更新
                        progress_bar.progress(
                            processed_rules_count / total_rules_count
                        )

                    new_sections.append(section)

                final_files[file_name] = "\n".join(new_sections)

            st.success("🎉 すべての例規への要約付与が完了しました！")

            # 完成Zipファイルの作成・ダウンロード
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for fname, fcontent in final_files.items():
                    zf.writestr(fname, fcontent.encode("utf-8"))

            st.download_button(
                label="📦 完成したナレッジZipファイルをダウンロード",
                data=zip_buffer.getvalue(),
                file_name="reiki_knowledge_summary_added.zip",
                mime="application/zip",
            )
