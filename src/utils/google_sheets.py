"""
Утилита для скачивания Google Sheets файлов в формате Excel
"""

from pathlib import Path
from typing import Optional
import io
from loguru import logger

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


class GoogleSheetsDownloader:
    """Скачивание Google Sheets файлов в формате Excel"""

    def __init__(self, credentials_path: Path):
        """
        Args:
            credentials_path: Путь к JSON файлу с service account credentials
        """
        self.credentials_path = credentials_path
        self.service = None

    def _authenticate(self):
        """Аутентификация через Service Account"""
        try:
            logger.info(f"Аутентификация через {self.credentials_path}")

            # Области доступа для Google Sheets и Google Drive
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets.readonly',
                'https://www.googleapis.com/auth/drive.readonly'
            ]

            # Создаем credentials из JSON файла
            credentials = Credentials.from_service_account_file(
                str(self.credentials_path),
                scopes=scopes
            )

            # Создаем service
            self.service = build('drive', 'v3', credentials=credentials)

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
        if not self.service:
            self._authenticate()

        try:
            logger.info(f"Скачивание Google Sheets ID: {spreadsheet_id}")

            # Экспортируем в формат Excel (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)
            request = self.service.files().export_media(
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
        if not self.service:
            self._authenticate()

        try:
            file_metadata = self.service.files().get(
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
