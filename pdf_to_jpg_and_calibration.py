'''конвертация и корректировка изображения'''

import logging

import cv2
import fitz
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

# ширина страницы в пикселях, под неё подобраны пороги выравнивания по линиям
TARGET_WIDTH_PX = 3000


def convert_file_to_massive(input_data, page_num: int = 0) -> np.ndarray | None:
    """
    Конвертация страницы PDF или изображения JPG/PNG в NumPy-массив RGB

    Вход:
        input_data: открытый PDF (fitz.Document) или байты изображения
        page_num: номер страницы PDF (с 0), для изображения не используется

    Выход:
        изображение RGB или None при ошибке
    """
    try:
        if isinstance(input_data, fitz.Document):
            page = input_data[page_num]
            dpi = max(300, int(TARGET_WIDTH_PX / page.rect.width * 72))
            pix = page.get_pixmap(dpi=dpi)
            image_array = np.array(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
            logger.info('Страница %d PDF преобразована в изображение %dx%d px (%d dpi)',
                        page_num + 1, pix.width, pix.height, dpi)
            return image_array

        image_array = cv2.imdecode(np.frombuffer(input_data, np.uint8), cv2.IMREAD_COLOR)
        if image_array is None:
            raise ValueError('не удалось декодировать изображение')
        h, w = image_array.shape[:2]
        scale = TARGET_WIDTH_PX / w
        image_array = cv2.resize(image_array, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
        logger.info('Изображение %dx%d px масштабировано до %dx%d px',
                    w, h, image_array.shape[1], image_array.shape[0])
        return cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)

    except Exception as e:
        logger.error('Ошибка конвертации страницы %d: %s', page_num + 1, e)
        return None


def correct_image_orientation(image_array: np.ndarray) -> np.ndarray:
    """
    Грубая корректировка ориентации (кратно 90°) через Tesseract OSD

    Вход:
        image_array: изображение RGB

    Выход:
        повёрнутое изображение или исходное, если поворот не нужен или OSD не сработал
    """
    try:
        # модель osd, а не rus: OSD нужен legacy-движок, которого нет в LSTM-модели rus из пакетов Debian
        osd = pytesseract.image_to_osd(Image.fromarray(image_array), lang='osd',
                                       output_type=pytesseract.Output.DICT)
        rotation_angle = osd['rotate']
        if rotation_angle == 0:
            logger.info('Tesseract: изображение уже в правильной ориентации')
            return image_array
        logger.info('Tesseract: изображение повёрнуто на %d° (уверенность %.2f)',
                    rotation_angle, osd['orientation_conf'])
        return np.array(Image.fromarray(image_array).rotate(-rotation_angle, expand=True))

    except Exception as e:
        logger.warning('Tesseract: ориентация не определена, страница оставлена как есть: %s', e)
        return image_array


def correct_image_orientation_lines(image_array: np.ndarray,
                                    min_line_length: int = 250,
                                    precision: float = 1,
                                    max_rotation: float = 10.0,
                                    step: float = 0.5) -> np.ndarray:
    """
    Точная корректировка наклона по первой найденной горизонтальной линии

    Вход:
        image_array: изображение RGB
        min_line_length: минимальная длина линии (px)
        precision: допустимая разница по Y концов линии после поворота (px)
        max_rotation: максимальный угол поворота (°)
        step: шаг перебора угла (°)

    Выход:
        выровненное изображение или исходное, если линия не найдена
    """
    try:
        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # отступ 500 px сверху и снизу: не выравниваемся по логотипам в шапке и краям скана
        h, w = image_array.shape[:2]
        lines = cv2.HoughLinesP(binary[500:h - 500, :], 1, np.pi / 180, threshold=100,
                                minLineLength=min_line_length, maxLineGap=5)
        if lines is None:
            logger.info('Line: линии не найдены, выравнивание пропущено')
            return image_array

        best_line = None
        for line in lines:
            x1, y1, x2, y2 = line[0]
            y1, y2 = y1 + 500, y2 + 500
            angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
            if abs(angle) < 5 or abs(abs(angle) - 180) < 5:
                best_line = (x1, y1, x2, y2)
                break
        if best_line is None:
            logger.info('Line: горизонтальные линии не найдены, выравнивание пропущено')
            return image_array

        x1, y1, x2, y2 = best_line
        line_points = np.array([[x1, y1], [x2, y2]], dtype='float32').reshape(-1, 1, 2)
        best_angle, min_diff = 0, abs(y1 - y2)
        for angle in np.arange(-max_rotation, max_rotation + step, step).tolist():
            rot_mat = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
            rotated = cv2.transform(line_points, rot_mat).reshape(-1, 2)
            diff = abs(float(rotated[0, 1]) - float(rotated[1, 1]))
            if diff < min_diff:
                best_angle, min_diff = angle, diff
            if min_diff < precision:
                break

        if min_diff >= precision:
            logger.info('Line: не удалось достичь точности %s px, выравнивание пропущено', precision)
            return image_array
        if best_angle == 0:
            logger.info('Line: наклон не обнаружен')
            return image_array

        rot_mat = cv2.getRotationMatrix2D((w / 2, h / 2), best_angle, 1)
        logger.info('Line: изображение выровнено на %.1f°', best_angle)
        return cv2.warpAffine(image_array, rot_mat, (w, h), borderValue=(255, 255, 255))

    except Exception as e:
        logger.warning('Line: ошибка выравнивания, страница оставлена как есть: %s', e)
        return image_array
