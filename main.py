import logging
import os
import time
from datetime import datetime
from typing import Callable

import fitz

from parse_requisites import REQUISITE_FIELDS, parse_requisites
from pdf_to_jpg_and_calibration import convert_file_to_massive, correct_image_orientation, correct_image_orientation_lines
from read_text import ocr_data

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png')

# 1xx — ошибка входного файла (обработка прерывается)
# 2xx — ошибка страницы (код страницы, остальные страницы обрабатываются)
# 3xx — результат распознавания, 500 — непредвиденная ошибка
ERRORS = {
    0: 'Обработано успешно',
    101: 'Неподдерживаемый формат файла',
    102: 'Файл пустой',
    103: 'Файл повреждён или не может быть открыт',
    104: 'PDF защищён паролем',
    201: 'Не удалось преобразовать страницу в изображение',
    202: 'Ошибка распознавания текста (OCR)',
    203: 'На странице не найден текст',
    301: 'Реквизит не прошёл проверку контрольной суммы',
    302: 'Реквизиты не найдены',
    303: 'Текст не распознан ни на одной странице',
    500: 'Внутренняя ошибка сервиса',
}


class ProcessingError(Exception):
    def __init__(self, code: int):
        super().__init__(ERRORS[code])
        self.code = code


def process_document(file_bytes: bytes, file_name: str, return_text: bool = False,
                     on_progress: Callable[[float, str], None] | None = None) -> dict:
    """
    Обработка документа: выравнивание, OCR и поиск банковских реквизитов на каждой странице

    Вход:
        file_bytes: содержимое файла PDF/JPG/PNG
        file_name: имя файла, по расширению определяется формат
        return_text: вернуть распознанный текст каждой страницы в поле text
        on_progress: callback(доля выполнения 0..1, описание этапа) для статусбара

    Выход:
        JSON-совместимый словарь: итог по документу, сводка реквизитов со всех страниц
        и результат по каждой странице в pages
    """
    report = on_progress or (lambda value, message: None)
    started = time.monotonic()
    result = {
        'document_name': file_name,
        'processed_at': None,
        'code': 0,
        'message': None,
        'requisites': {field: [] for field in REQUISITE_FIELDS},
        'pages': [],
    }
    doc = None

    def set_page_error(page_result: dict, code: int, **extra):
        page_result.update(code=code, message=ERRORS[code], **extra)
        logger.warning('"%s", страница %d: код %d (%s) %s',
                       file_name, page_result['page'], code, ERRORS[code], extra or '')

    logger.info('"%s": получен файл %d байт, вернуть текст: %s', file_name, len(file_bytes), return_text)
    try:
        extension = os.path.splitext(file_name)[1].lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ProcessingError(101)
        if not file_bytes:
            raise ProcessingError(102)

        if extension == '.pdf':
            try:
                doc = fitz.open(stream=file_bytes, filetype='pdf')
            except Exception as e:
                logger.error('"%s": не удалось открыть PDF: %s', file_name, e)
                raise ProcessingError(103)
            if doc.needs_pass:
                raise ProcessingError(104)
            if len(doc) == 0:
                raise ProcessingError(103)
        total = len(doc) if doc is not None else 1
        logger.info('"%s": формат %s, страниц: %d', file_name, extension, total)

        for page_num in range(total):
            page = page_num + 1
            stage = f'Страница {page} из {total}'

            report(page_num / total, f'{stage}: подготовка изображения')
            np_image = convert_file_to_massive(doc if doc is not None else file_bytes, page_num)
            if np_image is None and doc is None:
                raise ProcessingError(103)
            page_result = {'page': page, 'code': 0, 'message': ERRORS[0],
                           'requisites': {field: [] for field in REQUISITE_FIELDS}, 'warnings': []}
            if return_text:
                page_result['text'] = ''
            result['pages'].append(page_result)
            if np_image is None:
                set_page_error(page_result, 201)
                continue

            report((page_num + 0.2) / total, f'{stage}: выравнивание ориентации')
            np_image = correct_image_orientation(np_image)
            np_image = correct_image_orientation_lines(np_image, min_line_length=400)

            report((page_num + 0.4) / total, f'{stage}: распознавание текста')
            try:
                text = ocr_data(np_image).strip()
            except Exception as e:
                set_page_error(page_result, 202, error=str(e))
                continue
            if return_text:
                page_result['text'] = text
            if not text:
                set_page_error(page_result, 203)
                continue

            report((page_num + 0.9) / total, f'{stage}: поиск реквизитов')
            page_result['requisites'], invalid = parse_requisites(text)
            for field, value in invalid:
                logger.warning('"%s", страница %d: %s %s не прошёл проверку контрольной суммы',
                               file_name, page, field, value)
                page_result['warnings'].append({'code': 301, 'message': ERRORS[301], 'field': field, 'value': value})
            for field, values in page_result['requisites'].items():
                result['requisites'][field] += [v for v in values if v not in result['requisites'][field]]
            if not any(page_result['requisites'].values()):
                page_result.update(code=302, message=ERRORS[302])
            logger.info('"%s", страница %d: найдено реквизитов %s', file_name, page,
                        {field: len(values) for field, values in page_result['requisites'].items()})

        if all(page_result['code'] // 100 == 2 for page_result in result['pages']):
            raise ProcessingError(303)
        if not any(result['requisites'].values()):
            result['code'] = 302

    except ProcessingError as e:
        result['code'] = e.code
    except Exception:
        logger.exception('"%s": непредвиденная ошибка', file_name)
        result['code'] = 500
    finally:
        if doc is not None:
            doc.close()

    result['message'] = ERRORS[result['code']]
    result['processed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    report(1.0, 'Обработка завершена')
    logger.info('"%s": обработка завершена за %.1f с, код %d (%s), страниц: %d, из них с ошибками: %d',
                file_name, time.monotonic() - started, result['code'], result['message'], len(result['pages']),
                sum(page_result['code'] // 100 == 2 for page_result in result['pages']))
    return result
