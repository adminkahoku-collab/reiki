import streamlit as st
import zipfile
import io
import re
from bs4 import BeautifulSoup

st.set_page_config(page_title="例規13編自動分割ツール", page_icon="⚖")
st.title("⚖️ 例規13編ファイル自動分割・Dify軽量化ツール")

password = st.text_input("職員用パスワードを入力してください", type="password")

if password == "reiki063215":
    st.success("認証されました。")
    uploaded_zip = st.file_uploader("例規データのZIPファイルをアップロードしてください", type=["zip"])

    if uploaded_zip is not None:
        try:
            zip_buffer = io.BytesIO(uploaded_zip.read())
            
            with zipfile.ZipFile(zip_buffer, "r") as z:
                all_files = z.namelist()
                
                # 目次ファイルと本文ファイルの探索
                target_bunya = next((f for f in all_files if f.split("/")[-1].lower() in ["bunya_0010000.html", "bunya0010000.html"]), None)
                j_files = {f.split("/")[-1].replace("_J.html", "").replace("_j.html", ""): f for f in all_files if f.lower().endswith("_j.html")}

                if target_bunya:
                    if st.button("13編分割データ（ZIP）を作成する"):
                        bunya_bytes = z.read(target_bunya)
                        try:
                            bunya_html = bunya_bytes.decode("cp932")
                        except UnicodeDecodeError:
                            bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

                        bunya_soup = BeautifulSoup(bunya_html, "html.parser")
                        
                        # JavaScript呼び出しパターン (OpenResDataWin('xxxx'))
                        re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                        # 「第〇編」判定用パターン (全角・半角・数字に対応)
                        re_hen = re.compile(r"第\s*[0-9０-９一二三四五六七八九十]+\s*編\s*.*")

                        hen_data = {}
                        current_hen = "00_未分類"
                        hen_counter = 0

                        # HTML要素を全走査（STRONGタグやTABLEタグを含むすべての要素）
                        # find_all で出現順に要素をループ
                        for elem in bunya_soup.find_all(['strong', 'tr', 'p', 'div']):
                            
                            # STRONGタグなどで見出しがまとめて入っている場合の処理
                            if elem.name == 'strong':
                                # <BR/> で区切られている文章を1行ずつ分割
                                lines = [line.strip() for line in elem.get_text("\n", strip=True).split("\n") if line.strip()]
                                for line in lines:
                                    hen_match = re_hen.search(line)
                                    if hen_match:
                                        matched_text = hen_match.group(0).strip()
                                        clean_text = re.sub(r'[\\/:*?"<>|]', '_', matched_text)
                                        
                                        # 新しい編が検出された場合のみカウントアップ
                                        if not current_hen.endswith(clean_text):
                                            hen_counter += 1
                                            current_hen = f"{hen_counter:02d}_{clean_text}"
                                            if current_hen not in hen_data:
                                                hen_data[current_hen] = []

                            # TRタグなど（テーブル行）で例規リンクが含まれる場合の処理
                            elif elem.name == 'tr':
                                for a_tag in elem.find_all('a'):
                                    onclick_attr = a_tag.get('href', '') or a_tag.get('onclick', '')
                                    match = re_link.search(onclick_attr)
                                    if match:
                                        doc_id = match.group(1)
                                        title = a_tag.get_text(strip=True)
                                        
                                        if title and doc_id:
                                            if current_hen not in hen_data:
                                                hen_data[current_hen] = []
                                            
                                            # 重複追加の防止
                                            if not any(d['id'] == doc_id for d in hen_data[current_hen]):
                                                hen_data[current_hen].append({"id": doc_id, "title": title})

                        # 空の「00_未分類」があれば削除
                        if "00_未分類" in hen_data and len(hen_data["00_未分類"]) == 0:
                            del hen_data["00_未分類"]

                        # ZIP出力の準備
                        output_zip_buffer = io.BytesIO()
                        
                        with zipfile.ZipFile(output_zip_buffer, "w", zipfile.ZIP_DEFLATED) as out_zip:
                            total_docs = 0
                            
                            for hen_name, items in hen_data.items():
                                if not items:
                                    continue
                                
                                hen_markdown = f"# {hen_name}\n\n"
                                
                                for item in items:
                                    doc_id = item['id']
                                    rule_title = item['title']
                                    
                                    if doc_id in j_files:
                                        j_bytes = z.read(j_files[doc_id])
                                        try:
                                            j_html = j_bytes.decode("cp932")
                                        except UnicodeDecodeError:
                                            j_html = j_bytes.decode("utf-8", errors="ignore")

                                        j_soup = BeautifulSoup(j_html, "html.parser")
                                        for tag in j_soup(['script', 'style', 'noscript']):
                                            tag.decompose()

                                        raw_text = j_soup.get_text(separator="\n", strip=True)
                                        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

                                        # 重複タイトルの自動削除
                                        cleaned_lines = []
                                        title_pattern = re.compile(rf"^(○)?{re.escape(rule_title)}$")
                                        for line in lines:
                                            if not title_pattern.match(line):
                                                cleaned_lines.append(line)

                                        cleaned_text = "\n".join(cleaned_lines)
                                        hen_markdown += f"## {rule_title}\n\n{cleaned_text}\n\n---\n\n"
                                        total_docs += 1
                                
                                # 編ごとのMarkdownをZIPに追加
                                file_filename = f"{hen_name}.md"
                                out_zip.writestr(file_filename, hen_markdown.encode("utf-8"))

                        st.success(f"🎉 処理完了! 合計 {len(hen_data)} つの分類（{total_docs} 件の例規）のマークダウンを生成しました。")

                        # 分割されたファイル一覧のプレビュー
                        with st.expander("生成されたファイル一覧を確認"):
                            for h_name, h_items in hen_data.items():
                                st.write(f"📁 **{h_name}.md** ({len(h_items)} 件)")

                        # ZIPダウンロードボタン
                        st.download_button(
                            label="13編分割済みMarkdown (ZIP) をダウンロード",
                            data=output_zip_buffer.getvalue(),
                            file_name="reiki_13hen_markdowns.zip",
                            mime="application/zip"
                        )

        except Exception as e:
            st.error(f"エラーが発生しました: {str(e)}")

else:
    if password:
        st.error("パスワードが違います。")
    else:
        st.warning("正しいパスワードを入力すると操作パネルが表示されます。")
