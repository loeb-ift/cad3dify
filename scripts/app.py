import argparse
import hashlib
import io
import json
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError

load_dotenv()
from cad3dify import generate_step_from_2d_cad_image
from cad3dify.local_ocr import inspect_region, reviewed_context


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model_type', type=str, default='gpt')
    return parser.parse_args()


args = parse_args()
st.title('2D工程圖 → 3D CAD')
uploaded_file = st.sidebar.file_uploader('選擇工程圖', type=['jpg','jpeg','png'])

if uploaded_file is not None:
    try:
        Image.MAX_IMAGE_PIXELS = 600_000_000
        with Image.open(io.BytesIO(uploaded_file.getvalue())) as source:
            original_size = source.size
            source.thumbnail((4096,4096), Image.Resampling.LANCZOS)
            image = source.convert('RGB')
        st.image(image, caption='工程圖預覽', use_container_width=True)
        st.write('原始尺寸：', original_size)
        st.write('處理尺寸：', image.size)
        source_bytes = uploaded_file.getvalue()
        source_key = hashlib.sha256(source_bytes).hexdigest()
        local_observations = ''
        with st.expander('局部 OCR（選用，確認後才補充給生成器）'):
            st.caption('座標以原始圖片左上角為 (0, 0)，右、下邊界不包含在裁切內。OCR 不會自動確認尺寸。')
            columns = st.columns(4)
            values = [0, 0, original_size[0], original_size[1]]
            labels = ['左 x', '上 y', '右 x', '下 y']
            crop_box = [int(col.number_input(label, min_value=0,
                        max_value=original_size[i % 2], value=value, step=1,
                        key=f'ocr_box_{source_key}_{i}'))
                        for i, (col, label, value) in enumerate(zip(columns, labels, values))]
            mode = st.selectbox('文字排列', ['稀疏工程圖', '單一文字區塊'])
            psm = 11 if mode == '稀疏工程圖' else 6
            if st.button('執行局部 OCR'):
                st.session_state.pop('local_ocr', None)
                try:
                    with st.spinner('正在以 Tesseract 讀取原圖裁切...'):
                        report, crop_bytes = inspect_region(source_bytes, crop_box, psm=psm)
                    st.session_state['local_ocr'] = {'report': report, 'crop': crop_bytes}
                except (RuntimeError, ValueError) as exc:
                    st.error(str(exc))
            ocr = st.session_state.get('local_ocr')
            if ocr and ocr['report']['source_sha256'] == source_key:
                report = ocr['report']
                st.image(ocr['crop'], caption=f"原始解析度裁切：{report['crop_box']}")
                st.caption(f"實際執行：{report['engine']}；辨識模式 {report['psm']}。{report['note']}")
                if report['observations']:
                    rows = [{'confirmed': False, 'id': row['id'], 'raw_text': row['raw_text'],
                             'reviewed_text': row['raw_text'], 'confidence': row['confidence'],
                             'source_box': str(row['source_box'])} for row in report['observations']]
                    edited = st.data_editor(rows, hide_index=True,
                        disabled=['id', 'raw_text', 'confidence', 'source_box'],
                        column_config={'confirmed': st.column_config.CheckboxColumn('確認採用'),
                                       'reviewed_text': '核對／修正後文字', 'raw_text': 'OCR 原文'},
                        key='ocr_review_' + hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest())
                    local_observations = reviewed_context(report, edited, source_bytes)
                    st.caption('只有勾選「確認採用」的列會補充給生成器。')
                    st.download_button('下載 OCR 來源與核對紀錄',
                        json.dumps({'ocr': report, 'review': edited}, ensure_ascii=False, indent=2),
                        file_name='local-ocr-evidence.json', mime='application/json')
                else:
                    st.warning('OCR 已執行，但未辨識到文字。請縮小區域或改用單一文字區塊；不會補充任何資訊。')
        key = hashlib.sha256(source_bytes+args.model_type.encode()+local_observations.encode()).hexdigest()
        if st.button('產生 STEP', type='primary'):
            st.session_state.pop('cad_result', None)
            with tempfile.TemporaryDirectory(prefix='cad3dify_') as tmpdir:
                input_path, output_path = Path(tmpdir)/'input.png', Path(tmpdir)/'output.step'
                image.save(input_path, format='PNG')
                with st.spinner('正在產生 3D CAD...'):
                    generate_step_from_2d_cad_image(str(input_path), str(output_path), model_type=args.model_type,
                                                  local_observations=local_observations)
                if output_path.is_file() and output_path.stat().st_size:
                    st.session_state['cad_result'] = {'key':key,'step':output_path.read_bytes()}
                else:
                    st.error('沒有產生 STEP。')
        saved = st.session_state.get('cad_result')
        if saved and saved['key']==key:
            st.success('STEP 已產生並通過基本幾何檢查；尚未驗證與工程圖一致。')
            st.download_button('下載 STEP', saved['step'], file_name='output.step', mime='application/octet-stream')
    except (Image.DecompressionBombError, UnidentifiedImageError) as exc:
        st.error(f'圖片無法載入：{exc}')
    except MemoryError:
        st.error('圖片過大，記憶體不足。')
    except Exception as exc:
        st.exception(exc)
else:
    st.info('請先上傳工程圖。')
