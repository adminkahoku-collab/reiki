import streamlit as st
import zipfile
import io
import re
from bs4 import BeautifulSoup

st.set_page_config(page_title="例規13編自動分割ツール", page_icon="⚖")
st.title("⚖️ 例規13編ファイル自動分割・Dify軽量化ツール")


# --- 検索補強用キーワード自動生成関数 ---
def generate_search_keywords(title: str, content: str) -> str:
    """
    例規タイトルおよび本文から、BM25検索を補強するキーワードを自動抽出・付与する関数
    """
    keywords = set()
    
    # 1. タイトルから特定接頭辞・接尾辞を除去してコア単語を取得
    clean_title = re.sub(r'^(河北町|条例|規則|規程|要綱|基準|細則)', '', title)
    clean_title = re.sub(r'(条例|規則|規程|要綱|基準|細則)$', '', clean_title)
    if clean_title:
        keywords.add(clean_title)
    
    # 2. 自治体用語の同義語・関連語マッピング辞書
    synonym_map = {
        "弔慰": ["弔慰金", "死亡", "香典", "弔詞", "町長", "副町長", "特別職", "職員", "支給", "遺族", "死亡給付"],
        "災害弔慰": ["災害弔慰金", "被災", "災害見舞金", "支給", "市民"],
        "給与": ["手当", "報酬", "給料", "支給", "期末手当", "勤勉手当"],
        "旅費": ["出張", "日当", "宿泊料", "交通費"],
        "表彰": ["褒賞", "表彰状", "功労"],
        "公印": ["印鑑", "職印", "公印保管"],
        "文書": ["起案", "決裁", "保存期間", "公文書"],
        "議会": ["議員", "定例会", "臨時会", "委員会"],
        "財務": ["予算", "決算", "会計", "契約", "入札"],
        "税": ["町民税", "固定資産税", "軽自動車税", "課税", "納税"],
        "福祉": ["介護", "障害", "民生委員", "児童手当", "生活保護"],
        "環境": ["ゴミ", "廃棄物", "清掃", "公害"]
    }
    
    # タイトルまたは本文にマッチするキーがあれば関連語を追加
    for key, syn_list in synonym_map.items():
        if key in title or key in content:
            keywords.update(syn_list)
            
    return " ".join(keywords)


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
                        
                        # JavaScript呼び出しパターン
                        re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                        # 「第〇編」判定用パターン
                        re_hen = re.compile(r"第\s*[0-9０-９一二三四五六七八九十]+\s*編\s*.*")

                        hen_data = {}
                        current_hen = "00_未分類"
                        hen_counter = 0

                        # HTML要素を全走査
                        for elem in bunya_soup.find_all(['strong', 'tr', 'p', 'div']):
                            if elem.name == 'strong':
                                lines = [line.strip() for line in elem.get_text("\n", strip=True).split("\n") if line.strip()]
                                for line in lines:
                                    hen_match = re_hen.search(line)
                                    if hen_match:
                                        matched_text = hen_match.group(0).strip()
                                        clean_text = re.sub(r'[\\/:*?"<>|]', '_', matched_text)
                                        
                                        if not current_hen.endswith(clean_text):
                                            hen_counter += 1
                                            current_hen = f"{hen_counter:02d}_{clean_text}"
                                            if current_hen not in hen_data:
                                                hen_data[current_hen] = []

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
                                            
                                            if not any(d['id'] == doc_id for d in hen_data[current_hen]):
                                                hen_data[current_hen].append({"id": doc_id, "title": title})

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
                                        
                                        # 【追加ポイント】検索補強キーワードの自動生成
                                        keywords = generate_search_keywords(rule_title, cleaned_text)
                                        
                                        # 【追加ポイント】各例規ブロックの直前に HTMLコメント形式で埋め込み
                                        hen_markdown += f"<!-- 検索キーワード: {keywords} -->\n"
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
