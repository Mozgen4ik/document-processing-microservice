import json
import logging
import os

import streamlit as st

from main import process_document

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'),
                    format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
logger = logging.getLogger('app')

st.set_page_config(page_title='Распознавание реквизитов', page_icon='📄')
st.title('Демоверсия сервиса распознавания')
st.caption('Выравнивание страниц, OCR и поиск банковских реквизитов')

with st.form('upload_form'):
    uploaded_files = st.file_uploader('Документы (PDF, JPG, PNG)', type=['pdf', 'jpg', 'jpeg', 'png'],
                                      accept_multiple_files=True)
    button_col, flag_col = st.columns(2, vertical_alignment='center')
    submitted = button_col.form_submit_button('Отправить на распознавание', type='primary')
    return_text = flag_col.checkbox('Вернуть распознанный текст')

if submitted:
    st.session_state.pop('results', None)
    if not uploaded_files:
        st.warning('Выберите файлы для распознавания')
    else:
        total = len(uploaded_files)
        logger.info('Получено файлов на обработку: %d', total)
        progress = st.progress(0.0, text='Загрузка файлов')
        results = []
        # файлы обрабатываются последовательно, прогресс общий по всем файлам
        for index, uploaded_file in enumerate(uploaded_files):
            results.append(process_document(
                uploaded_file.getvalue(), uploaded_file.name, return_text,
                on_progress=lambda value, message: progress.progress(
                    min((index + value) / total, 1.0),
                    text=f'Файл {index + 1} из {total} «{uploaded_file.name}». {message}'),
            ))
        st.session_state['results'] = results

# результаты хранятся в session_state, чтобы не пропадать при перезапуске скрипта по кнопкам «Скачать»
results = st.session_state.get('results')
if results:
    if len(results) > 1:
        st.download_button('Скачать все результаты в TXT', data=json.dumps(results, ensure_ascii=False, indent=2),
                           file_name='results.txt', mime='text/plain', key='download_all')

    for index, result in enumerate(results):
        with st.container(border=True):
            failed_pages = [page['page'] for page in result['pages'] if page['code'] // 100 == 2]
            summary = (f"**{result['document_name']}** — {result['message']} (код {result['code']}), "
                       f"страниц: {len(result['pages'])}")
            if failed_pages:
                summary += f", ошибки на стр. {', '.join(map(str, failed_pages))}"
            if result['code'] == 0:
                st.success(summary)
            elif result['code'] == 302:
                st.warning(summary)
            else:
                st.error(summary)

            st.json(result, expanded=True if len(results) == 1 else 1)
            st.download_button('Скачать в TXT', data=json.dumps(result, ensure_ascii=False, indent=2),
                               file_name=f"{os.path.splitext(result['document_name'])[0]}.txt",
                               mime='text/plain', key=f'download_{index}')
