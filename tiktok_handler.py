from services.downloaders import download_video


def download_tiktok_video(video_url, output_folder=None):
    result = download_video(video_url, output_folder=output_folder)
    return {
        "success": result.success,
        "filepath": result.filepath,
        "title": result.title,
        "error": result.error,
    }
