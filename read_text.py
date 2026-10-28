import logging
import time

import cv2
import numpy as np
import pytesseract

logger = logging.getLogger(__name__)

TESSERACT_LANG = 'rus'
# psm 4 — колонка текста разного размера: строки таблиц читаются целиком, подпись реквизита остаётся рядом со значением
TESSERACT_CONFIG_OCR = '--oem 3 --psm 4'


def ocr_data(image_array: np.ndarray) -> str:
    """
    OCR текста из изображения, перед распознаванием стираются линии таблиц

    Вход:
        image_array: изображение RGB для распознавания

    Выход:
        распознанный текст (ошибки Tesseract пробрасываются вызывающему)
    """
    gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)

    # с линиями таблиц Tesseract пропускает текст в ячейках; отрезки длиннее 1/20 ширины страницы
    # (150 px при 3000 px) длиннее штрихов любых символов до ~40pt
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(binary, 1, np.pi / 180, threshold=100, minLineLength=gray.shape[1] // 20, maxLineGap=5)
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            cv2.line(gray, (int(x1), int(y1)), (int(x2), int(y2)), 255, thickness=5)
    logger.info('OCR: стёрто отрезков линий: %d', 0 if lines is None else len(lines))

    started = time.monotonic()
    text = pytesseract.image_to_string(gray, lang=TESSERACT_LANG, config=TESSERACT_CONFIG_OCR)
    logger.info('OCR: распознано %d символов за %.1f с', len(text.strip()), time.monotonic() - started)
    return text
