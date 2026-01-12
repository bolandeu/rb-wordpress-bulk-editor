"""
Утилита для работы с Google Sheets файлами (скачивание и загрузка)
"""

from pathlib import Path
from typing import Optional, List
import io
from loguru import logger
import pandas as pd
from openpyxl import load_workbook

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


class GoogleSheetsClient:
    """Работа с Google Sheets (скачивание и загрузка)"""

    def __init__(self, credentials_path: Path):
        """
        Args:
            credentials_path: Путь к JSON файлу с service account credentials
        """
        self.credentials_path = credentials_path
        self.drive_service = None
        self.sheets_service = None

    def _authenticate(self):
        """Аутентификация через Service Account"""
        try:
            logger.info(f"Аутентификация через {self.credentials_path}")

            # Области доступа для Google Sheets и Google Drive (с правами записи)
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]

            # Создаем credentials из JSON файла
            credentials = Credentials.from_service_account_file(
                str(self.credentials_path),
                scopes=scopes
            )

            # Создаем services
            self.drive_service = build('drive', 'v3', credentials=credentials)
            self.sheets_service = build('sheets', 'v4', credentials=credentials)

            logger.info("Аутентификация успешна")

        except Exception as e:
            logger.error(f"Ошибка аутентификации: {e}")
            raise

    def download_as_excel(
        self,
        spreadsheet_id: str,
        output_path: Path
    ):
        """
        Скачать Google Sheets файл в формате Excel (.xlsx)

        Args:
            spreadsheet_id: ID Google Sheets документа
            output_path: Путь для сохранения .xlsx файла
        """
        if not self.drive_service:
            self._authenticate()

        try:
            logger.info(f"Скачивание Google Sheets ID: {spreadsheet_id}")

            # Экспортируем в формат Excel (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)
            request = self.drive_service.files().export_media(
                fileId=spreadsheet_id,
                mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )

            # Создаем буфер для скачивания
            file_buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(file_buffer, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug(f"Скачивание: {progress}%")

            # Сохраняем в файл
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'wb') as f:
                f.write(file_buffer.getvalue())

            logger.info(f"Файл успешно сохранен: {output_path}")

        except Exception as e:
            logger.error(f"Ошибка скачивания Google Sheets: {e}")
            raise

    def get_spreadsheet_info(self, spreadsheet_id: str) -> dict:
        """
        Получить информацию о Google Sheets документе

        Args:
            spreadsheet_id: ID Google Sheets документа

        Returns:
            Словарь с информацией о документе
        """
        if not self.drive_service:
            self._authenticate()

        try:
            file_metadata = self.drive_service.files().get(
                fileId=spreadsheet_id,
                fields='id, name, modifiedTime, owners'
            ).execute()

            return {
                'id': file_metadata.get('id'),
                'name': file_metadata.get('name'),
                'modified_time': file_metadata.get('modifiedTime'),
                'owners': file_metadata.get('owners', [])
            }

        except Exception as e:
            logger.error(f"Ошибка получения информации о документе: {e}")
            raise

    def upload_excel_to_sheets(
        self,
        excel_path: Path,
        spreadsheet_id: str,
        sheets_to_update: Optional[List[str]] = None
    ):
        """
        Загрузить данные из Excel файла в Google Sheets

        Args:
            excel_path: Путь к Excel файлу
            spreadsheet_id: ID Google Sheets документа
            sheets_to_update: Список листов для обновления (None = все листы)
        """
        if not self.sheets_service:
            self._authenticate()

        try:
            logger.info(f"Загрузка Excel файла в Google Sheets: {excel_path}")

            # Читаем Excel файл
            wb = load_workbook(excel_path, data_only=True)

            # Получаем информацию о существующих листах в Google Sheets
            spreadsheet_metadata = self.sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id
            ).execute()

            existing_sheets = {
                sheet['properties']['title']: sheet['properties']['sheetId']
                for sheet in spreadsheet_metadata.get('sheets', [])
            }

            # Определяем какие листы нужно обновить
            sheets_to_process = sheets_to_update if sheets_to_update else wb.sheetnames

            logger.info(f"Листы для обновления: {sheets_to_process}")

            # Обрабатываем каждый лист
            for sheet_name in sheets_to_process:
                if sheet_name not in wb.sheetnames:
                    logger.warning(f"Лист '{sheet_name}' не найден в Excel файле, пропускаем")
                    continue

                logger.info(f"Обновление листа: {sheet_name}")

                # Читаем данные из Excel
                df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str)

                # Заменяем NaN на пустые строки
                df = df.fillna('')

                # Преобразуем в список списков (включая заголовки)
                values = [df.columns.tolist()] + df.values.tolist()

                # Проверяем существует ли лист в Google Sheets
                if sheet_name not in existing_sheets:
                    logger.info(f"Лист '{sheet_name}' не существует в Google Sheets, создаем...")
                    # Создаем новый лист
                    request_body = {
                        'requests': [{
                            'addSheet': {
                                'properties': {
                                    'title': sheet_name
                                }
                            }
                        }]
                    }
                    self.sheets_service.spreadsheets().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body=request_body
                    ).execute()
                    logger.info(f"Лист '{sheet_name}' создан")
                else:
                    # Очищаем существующий лист
                    logger.debug(f"Очистка листа '{sheet_name}'")
                    self.sheets_service.spreadsheets().values().clear(
                        spreadsheetId=spreadsheet_id,
                        range=f"'{sheet_name}'!A:ZZ"
                    ).execute()

                # Загружаем данные в Google Sheets
                logger.debug(f"Загрузка {len(values)} строк в лист '{sheet_name}'")
                body = {
                    'values': values
                }

                self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{sheet_name}'!A1",
                    valueInputOption='USER_ENTERED',
                    body=body
                ).execute()

                logger.info(f"Лист '{sheet_name}' успешно обновлен ({len(values)} строк)")

            logger.info("Все листы успешно обновлены в Google Sheets")

        except Exception as e:
            logger.error(f"Ошибка загрузки в Google Sheets: {e}")
            raise


# Обратная совместимость
GoogleSheetsDownloader = GoogleSheetsClient
