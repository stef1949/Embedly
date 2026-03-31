from services.downloaders import DownloadResult, download_video


def download_instagram_video(video_url: str, output_folder: str | None = None) -> DownloadResult:
    return download_video(video_url, output_folder=output_folder)
