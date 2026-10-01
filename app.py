import streamlit as st
import zipfile
import io
import os
import re
import json
from bs4 import BeautifulSoup

# 画面基本設定
st.set_page_config(page_title="例規データRAG変換ツール", page_icon="⚖️")
st.title("⚖️ 例規HTMLデータ ➔ RAGテキスト変換ツール")
st.write("DVDから抽出したZipファイルをアップロードすると、Dify用の最適化テキスト（Markdown）を生成してダウンロードできます。")

# 簡易パスワード認証
password = st.text_input("職員用パスワードを入力してください", type="password")

if password == "reiki063215":
    st.success("認証されました。")
    st.markdown("---")
    
    # 1. ZIPファイルのアップロード
    uploaded_zip = st.file_uploader("例規データのZIPファイルをアップロードしてください", type=["zip"])

    if uploaded_zip is not None:
        try:
            zip_buffer = io.BytesIO(uploaded_zip.read())
            
            with zipfile.ZipFile(zip_buffer, "r") as z:
                all_files = z.namelist()
                
                # --- 【ファイル特定の厳格化】 ---
                target_bunya_file = None
                for f in all_files:
                    filename = f.split("/")[-1].lower()
                    if filename in ["bunya_0010000.html", "bunya0010000.html"]:
                        target_bunya_file = f
                        break

                # 本文ファイル（_J.html）のリストアップ
                j_files = [f for f in all_files if f.endswith("_J.html") or f.endswith("_j.html")]

                # 情報表示
                st.subheader("📊 ZIP内の解析対象判定")
                col1, col2 = st.columns(2)
                
                if target_bunya_file:
                    col1.success(f"目次ファイル検出: `{target_bunya_file}`")
                else:
                    col1.error("❌ `bunya_0010000.html` が見つかりませんでした")

                col2.info(f"本文HTML (_J) 総数: {len(j_files)} 件")

                # 目次ファイルが見つかった場合のみ処理を実行
                if target_bunya_file and j_files:
                    if st.button("Dify用RAGデータ化（Markdown）を実行する"):
                        
                        # --- A. 目次ファイル (bunya_0010000.html) の解析 ---
                        bunya_bytes = z.read(target_bunya_file)
                        try:
                            bunya_html = bunya_bytes.decode("cp932")
                        except UnicodeDecodeError:
                            bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

                        bunya_soup = BeautifulSoup(bunya_html, "html.parser")
                        re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                        
                        categories = []
                        for elem in bunya_soup.find_all(['td', 'div', 'p', 'a']):
                            onclick_attr = elem.get('onclick', '') or elem.get('href', '')
                            match = re_link.search(onclick_attr)
                            if match:
                                doc_id = match.group(1)
                                title = elem.get_text(strip=True)
                                if title and doc_id:
                                    categories.append({"id": doc_id, "title": title})

                        # 重複IDの除去
                        seen = set()
                        unique_categories = []
                        for item in categories:
                            if item['id'] not in seen:
                                seen.add(item['id'])
                                unique_categories.append(item)

                        st.write(f"✅ `bunya_0010000.html` から **{len(unique_categories)} 件** の例規タイトルを取得しました。")

                        # --- B. 該当する _J.html のみを抽出・Markdownテキスト作成 ---
                        j_file_map = {}
                        for path in j_files:
                            filename = path.split("/")[-1]
                            doc_id = filename.replace("_J.html", "").replace("_j.html", "")
                            j_file_map[doc_id] = path

                        markdown_contents = []
                        missing_ids = []

                        progress_bar = st.progress(0)

                        for idx, item in enumerate(unique_categories):
                            doc_id = item['id']
                            
                            if doc_id in j_file_map:
                                j_bytes = z.read(j_file_map[doc_id])
                                try:
                                    j_html = j_bytes.decode("cp932")
                                except UnicodeDecodeError:
                                    j_html = j_bytes.decode("utf-8", errors="ignore")

                                j_soup = BeautifulSoup(j_html, "html.parser")
                                
                                for tag in j_soup(['script', 'style', 'noscript']):
                                    tag.decompose()

                                full_text = j_soup.get_text(separator="\n", strip=True)

                                # Dify用にMarkdown形式で整形（識別IDを除外し、タイトルと本文のみにする）
                                doc_markdown = f"# {item['title']}\n\n{full_text}\n\n---\n"
                                markdown_contents.append(doc_markdown)
                            else:
                                missing_ids.append(doc_id)

                            progress_bar.progress((idx + 1) / len(unique_categories))

                        st.success(f"🎉 処理完了! 成功: **{len(markdown_contents)} 件** / 未検出: **{len(missing_ids)} 件**")

                        if missing_ids:
                            with st.expander("`_J.html` が存在しなかったID一覧"):
                                st.write(missing_ids)

                        # --- C. 結果のプレビューとダウンロード ---
                        if markdown_contents:
                            # 1つの巨大テキストに結合
                            full_markdown_text = "\n".join(markdown_contents)

                            st.subheader("抽出データのプレビュー（先頭1件）")
                            st.code(markdown_contents[0], language="markdown")

                            # Difyにそのまま投入できるMarkdown (.md) ファイルとしてダウンロード
                            st.download_button(
                                label="Dify用Markdownデータをダウンロード (.md)",
                                data=full_markdown_text.encode("utf-8"),
                                file_name="reiki_rag_data.md",
                                mime="text/markdown"
                            )

        except Exception as e:
            st.error(f"ZIPファイルの処理中にエラーが発生しました: {str(e)}")

else:
    if password:
        st.error("パスワードが違います。")
    else:
        st.warning("正しいパスワードを入力すると操作パネルが表示されます。")
