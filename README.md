# PyFastDL (Async Smart Downloader) 🚀

A high-performance, asynchronous file downloader written in Python using `aiohttp`. It features a **smart hybrid strategy** that dynamically probes download speed to decide between single-chunk or multi-chunk downloading for optimal performance.

## ✨ Features

- **🧠 Smart Strategy:** Probes the server speed first. If it's fast enough, it downloads in one go to save overhead. If slow, it switches to multi-threaded downloading.
- **⚡ Asynchronous:** Built on `asyncio` and `aiohttp` for non-blocking I/O.
- **🔌 Session Injection:** Bring your own `aiohttp.ClientSession` (pass cookies, auth headers, proxies, etc.).
- **⏯ Resilient:** Automatic retries for failed chunks.
- **📉 Speed Limiting:** Optional bandwidth throttling.
- **💾 RAM Optimized:** Buffers writes to disk to minimize I/O operations.

## 📦 Installation

```bash
pip install --upgrade pyfastdl
```

## 🚀 Usage

### Basic Usage

```python
import asyncio
from pyfastdl import SmartDownloader

async def main():
    url = "https://link.testfile.org/100MB"
    downloader = SmartDownloader(url, "file.zip", max_connections=16)
    
    success = await downloader.download()
    if success:
        print("Download Complete!")

if __name__ == "__main__":
    asyncio.run(main())
```

### Advanced: Using Custom Session (Auth/Cookies)

```python
import asyncio
import aiohttp
from pyfastdl import SmartDownloader

async def main():
    headers = {"Authorization": "Bearer YOUR_TOKEN"}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        downloader = SmartDownloader(
            url="https://protected-server.com/file.zip",
            output_file="secure_file.zip"
        )
        # Pass the session to the downloader
        await downloader.download(session=session)

asyncio.run(main())
```

## ⚙️ Configuration

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `max_connections` | 16 | Max concurrent connections (chunks). |
| `min_split_size` | 10MB | Minimum file size required to enable splitting. |
| `write_buff_size` | 10MB | Size of data to buffer in RAM before writing to disk. |
| `max_speed_limit` | -1 | Limit download speed (bytes/s). -1 = Unlimited. |
| `progress_interval` | -1 | Interval (in seconds) to display download progress. -1 = Never.|
| `retry_attempts` | 3 | Number of retries for a failed chunk.|

## 🤝 Contributing

Pull requests are welcome! 

## 📝 License

MIT
