import asyncio
import aiofiles
import aiohttp
import logging
import os
import ssl
from time import monotonic


# --- Logging Setup ---

logger = logging.getLogger(__name__)

class SmartDownloader:
    """
    A smart downloader with a hybrid start strategy. It probes download speed first
    and decides between a single-chunk or multi-chunk strategy dynamically.
    """
    def __init__(self,
                 url: str,
                 output_file: str,
                 max_connections: int = 16,
                 verify_ssl: bool = True,
                 min_split_size: int = 10 * 1024 * 1024,      # 10MB
                 min_speed_for_split: int = 5 * 1024 * 1024,  # 5MB/s
                 speed_check_interval: int = 5, # every 5 Seconds                
                 retry_attempts: int = 3,
                 chunk_read_size: int = 128 * 1024 ,           # 64 KB
                 write_buff_size : int = 10 * 1024 * 1024,
                 max_speed_limit : int = -1,
                 progress_interval : int = -1):     
        """
        Initializes the SmartDownloader.

        Args:
            url: URL of the file to download.
            output_file: Path to save the downloaded file.
            max_connections: Maximum number of concurrent download connections.
            verify_ssl: Whether to verify the server's SSL certificate.
            min_split_size: Minimum size (in bytes) a chunk must be to be eligible for splitting.
            min_speed_for_split: Speed threshold (in bytes/sec) below which a chunk is split.
            speed_check_interval: Interval (in seconds) to check the download speed of a chunk. -1 means never
            retry_attempts: Number of retries for a failed chunk.
            chunk_read_size: Size (in bytes) of data to read from the socket at a time.
            write_buff_size : Size (in bytes) of data to write on file  at a time.
            max_speed_limit : Limit speed download to reduce resource usage if you care resources more than speed 
            progress_interval : Interval (in seconds) to display download progress.

        """
        self.url = url
        self.output_file = output_file
        self.max_connections = max_connections
        self.verify_ssl = verify_ssl
        self.min_split_size = min_split_size
        self.min_speed_for_split = min_speed_for_split
        self.speed_check_interval = speed_check_interval
        self.retry_attempts = retry_attempts
        self.chunk_read_size = chunk_read_size
        self.write_buff_size = write_buff_size
        self.progress_interval = progress_interval

        if max_speed_limit > 0:
            self.worker_speed_limit = max_speed_limit / max_connections
        else:
            self.worker_speed_limit = -1

        # Internal state
        self.file_size: int = 0
        self.downloaded_bytes: int = 0
        self.lock = asyncio.Lock()
        self.file_lock = asyncio.Lock()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers: list = []
        self.active_workers: int = 0
        self.download_finished: bool = False

        logger.info(f"Downloader initialized for {url} -> {output_file}")

    
    def get_default_headers(self) -> dict:
        """
        Returns default headers. Can be overridden by subclasses.
        """
        #Idm defualt headers 
        return {
            'user-agent': 'Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko',
            'accept-encoding' : 'identity',
            'accept-language' : 'en-US',
            'accept-charset' : '*'
        }

    def get_ssl_context(self):
        """
        Configures and returns the SSL context.
        """
        ssl_context = ssl.create_default_context()
        if not self.verify_ssl:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            logger.warning("SSL certificate verification is disabled.")
        return ssl_context

    def get_session_kwargs(self) -> dict:
        """
        Returns a dictionary of arguments to be passed to aiohttp.ClientSession.
        Useful for setting timeouts, cookies, or connectors.
        """
        # Connector settings
        connector = aiohttp.TCPConnector(
            limit=self.max_connections, 
            ssl=self.get_ssl_context()
        )
        
        # Timeout settings
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)

        # you can add other settings you want in your subclasses
        return {
            'connector': connector,
            'timeout': timeout,
            # 'cookies': {'session_id': '...'}
        }



    async def get_file_size(self, session: aiohttp.ClientSession) -> int:
        """Get file size with a GET request (for better compatibility)."""
        try:
            async with session.get(self.url,allow_redirects=True) as response:
                response.raise_for_status()
                size = int(response.headers.get('content-length', 0))
                logger.info(f"File size: {size / (1024*1024):.2f} MB")
                return size
        except Exception as e:
            logger.error(f"Error getting file size: {e}", exc_info=True)
            return 0

    async def create_file(self) -> None:
        """Create an empty file with specified size for multi-part download."""
        logger.info(f"Creating placeholder file")
        f = open(self.output_file, 'wb')
        f.close()


    """
        TODO Make this function return values to customize progress
    """
    async def speed_monitor(self) -> None:
        
        """Displays the overall download speed live."""
        
        last_bytes = 0
        start_time = monotonic()
        
        while not self.download_finished:
            await asyncio.sleep(self.progress_interval)
            
            # Avoid division by zero at the very beginning
            if monotonic() - start_time < self.progress_interval:
                continue
                
            current_bytes = self.downloaded_bytes
            speed = (current_bytes - last_bytes) / self.progress_interval
            last_bytes = current_bytes
            
            if self.file_size > 0:
                progress = (current_bytes / self.file_size) * 100
                downloaded_mb = current_bytes / (1024 * 1024)
                total_mb = self.file_size / (1024 * 1024)
                speed_mbps = speed / (1024 * 1024)
                print(f"\rSpeed: {speed_mbps:.2f} MB/s | Downloaded: {downloaded_mb:.2f}/{total_mb:.2f} MB ({progress:.1f}%)",end="")

    async def probe_and_initiate(self, session: aiohttp.ClientSession, f):
        """
        Tests the initial speed and decides the download strategy (single vs. multi-chunk).
        Writes the probed data to the file to avoid re-downloading.
        """
        probe_duration = 5  # seconds
        probe_max_bytes = 10 * 1024 * 1024  # 10 MB limit for probe

        logger.info(f"Probing initial download speed for ~{probe_duration} seconds...")
        
        try:
            headers = {'Range': f'bytes=0-{self.file_size - 1}'}
            current_pos = 0
            time_out = aiohttp.ClientTimeout(total=120,sock_read=120)
            async with session.get(self.url, headers=headers, timeout=time_out) as response:
                response.raise_for_status()
        
                time_start = monotonic()

                async for chunk_data in response.content.iter_chunked(self.chunk_read_size):
                    """ if not chunk_data:
                        break """
                    
                    async with self.file_lock:
                        await f.seek(current_pos)
                        await f.write(chunk_data)

                    chunk_len = len(chunk_data)
                    async with self.lock:
                        self.downloaded_bytes += chunk_len
                    current_pos += chunk_len
                    
                    elapsed = monotonic() - time_start
                    if elapsed > probe_duration or current_pos >= probe_max_bytes:
                        break

                elapsed = monotonic() - time_start
                speed = self.downloaded_bytes / elapsed if elapsed > 0 else 0
                logger.info(f"Probe result: Speed={speed / 1024**2:.2f} MB/s, Downloaded={current_pos / 1024**2:.2f} MB")

                if current_pos >= self.file_size - 1:
                    logger.info("File completely downloaded during initial probe.")
                    return

                # --- The Smart Decision ---
                if speed > self.min_speed_for_split:
                    logger.info("High initial speed. Continuing with a single chunk for the remainder.")
                    await self.queue.put((current_pos, self.file_size - 1, 0))
                else:
                    logger.info("Low initial speed. Splitting the remainder into multiple chunks.")
                    remaining_size = self.file_size - current_pos
                    chunk_size = remaining_size // self.max_connections
                    if chunk_size < self.min_split_size / 2:
                        await self.queue.put((current_pos, self.file_size - 1, 0))
                    else:
                        for i in range(self.max_connections):
                            start = current_pos + (i * chunk_size)
                            end = start + chunk_size - 1
                            if i == self.max_connections - 1:
                                end = self.file_size - 1
                            if start <= end:
                                await self.queue.put((start, end, 0))
        except Exception as e:
            logger.warning(f"Speed probe failed: {e}. Defaulting to multi-chunk strategy for the whole file.")
            # Fallback to multi-chunk if probe fails
            chunk_size = self.file_size // self.max_connections
            for i in range(self.max_connections):
                start = i * chunk_size
                end = start + chunk_size - 1
                if i == self.max_connections - 1: end = self.file_size - 1
                if start <= end: await self.queue.put((start, end, 0))


    async def download_chunk(self, session: aiohttp.ClientSession, start_byte: int, end_byte: int, attempts: int, f):
        headers = {'Range': f'bytes={start_byte}-{end_byte}'}
        current_pos = start_byte
        
        # Write buffer settings
        local_buffer = bytearray()
        write_start_pos = current_pos

        try:
    
            async with session.get(self.url, headers=headers) as response:
                response.raise_for_status()
                
                if response.status != 206 and start_byte > 0:
                     logger.warning(f"Server may not support range requests.")

                time_start = monotonic()
                bytes_downloaded_in_period = 0

                async for chunk_data in response.content.iter_chunked(self.chunk_read_size):
                    if not chunk_data: break
                    
                    # 1. Store only in RAM
                    local_buffer.extend(chunk_data)
                    chunk_len = len(chunk_data)
                    
                    async with self.lock:
                        self.downloaded_bytes += chunk_len
                    
                    current_pos += chunk_len
                    bytes_downloaded_in_period += chunk_len

                    # 2. If buffer is full, write to disk at once
                    if len(local_buffer) >= self.write_buff_size:
                        async with self.file_lock:
                            await f.seek(write_start_pos)
                            await f.write(local_buffer)
                        
                        # Reset buffer
                        write_start_pos = current_pos
                        local_buffer.clear()

                    # --- Smart chunk splitting logic ---
                    elapsed = monotonic() - time_start

                    #---=== Speed Limit checking ===----
                    if self.worker_speed_limit > 0:
                        expected_time = bytes_downloaded_in_period / self.worker_speed_limit
                        if elapsed < expected_time:
                            sleep_time = expected_time - elapsed
                            await asyncio.sleep(sleep_time)
                            continue

                    #---==== Speed Tune =====------

                    if elapsed >= self.speed_check_interval:
                        speed = bytes_downloaded_in_period / elapsed
                        remaining_size = end_byte - current_pos
                        is_splittable = remaining_size // 2 > self.min_split_size
                        can_spawn_worker = self.active_workers < self.max_connections

                        if speed < self.min_speed_for_split and is_splittable and can_spawn_worker:
                            # Before exiting, make sure to write the remaining buffer
                            if local_buffer:
                                async with self.file_lock:
                                    await f.seek(write_start_pos)
                                    await f.write(local_buffer)
                            
                            mid = current_pos + remaining_size // 2
                            logger.info(f"Low speed splitting chunk.")
                            await self.queue.put((current_pos, mid, 0))
                            await self.queue.put((mid + 1, end_byte, 0))
                            return

                        time_start = monotonic()
                        bytes_downloaded_in_period = 0
                
                # 3. Write remaining buffer at the end of download
                if local_buffer:
                    async with self.file_lock:
                        await f.seek(write_start_pos)
                        await f.write(local_buffer)

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Chunk failed: {e}")
            # Here too, if there was something in the buffer and an error occurred, since the file position hasn't been updated, no problem occurs
            # Just add the task back to the queue
            if current_pos <= end_byte:
                if attempts < self.retry_attempts:
                    await self.queue.put((current_pos, end_byte, attempts + 1))
        except Exception as e:
            logger.error(f"Fatal error: {e}")


    async def worker(self, session: aiohttp.ClientSession, f):
        """A worker that continuously picks chunks from the queue and downloads them."""
        while True:
            chunk_info = await self.queue.get()
            if chunk_info is None: break

            start, end, attempts = chunk_info
            
            async with self.lock: self.active_workers += 1
            try:
                logger.debug(f'Worker starts downloading chunk {start}-{end}.')
                await self.download_chunk(session, start, end, attempts, f)
            finally:
                async with self.lock: self.active_workers -= 1
                self.queue.task_done()
                logger.debug(f'Worker finished task for chunk {start}-{end}.')

    async def download(self, session: aiohttp.ClientSession | None = None) -> bool:
        """Main function to manage the download process."""
        managed_session = False
        if session is None:
            # Call override methods
            headers = self.get_default_headers()
            session_kwargs = self.get_session_kwargs()
            #Create session wuth dynamic settings
            session = aiohttp.ClientSession(headers=headers, **session_kwargs)
            managed_session = True
        try:
            async with session:
                self.file_size = await self.get_file_size(session)
                if not self.file_size:
                    logger.critical("Could not determine file size. Aborting.")
                    return False

                await self.create_file()
                start_time = monotonic()
                monitor_task = None
                if self.progress_interval > -1 :
                    monitor_task = asyncio.create_task(self.speed_monitor())

                async with aiofiles.open(self.output_file, 'rb+') as f:
                    # --- NEW HYBRID LOGIC ---
                    await self.probe_and_initiate(session, f)
                    
                    self.workers = [asyncio.create_task(self.worker(session, f)) for _ in range(self.max_connections)]
                    await self.queue.join()

                    self.download_finished = True
                    if monitor_task:
                        await monitor_task

                    for _ in self.workers: await self.queue.put(None)
                    await asyncio.gather(*self.workers, return_exceptions=True)

                total_time = monotonic() - start_time
                logger.info("\n--- Download Summary ---")

                final_size = os.path.getsize(self.output_file)
                logger.info(f"Total bytes downloaded via counter: {self.downloaded_bytes}")
                logger.info(f"Final file size on disk: {final_size}")

                if self.downloaded_bytes >= self.file_size and final_size == self.file_size:
                    avg_speed = self.file_size / total_time / (1024 * 1024)
                    logger.info(f"✅ Download successful! Finished in {total_time:.2f}s. Average speed: {avg_speed:.2f} MB/s")
                    return True
                else:
                    logger.info(f"❌ Download failed: Size mismatch.\nExpected: {self.file_size}\nDownloaded: {self.downloaded_bytes}\nOn Disk: {final_size}")
                    return False
        finally:
            if managed_session: await session.close()

