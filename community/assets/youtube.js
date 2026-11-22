document.querySelectorAll('[data-video-id]').forEach(shell => {
  const button = shell.querySelector('button');
  button?.addEventListener('click', () => {
    const id = shell.dataset.videoId;
    if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return;
    const iframe = document.createElement('iframe');
    iframe.src = `https://www.youtube-nocookie.com/embed/${id}?playsinline=1&rel=0`;
    iframe.title = shell.dataset.videoTitle || 'YouTube 影片';
    iframe.loading = 'lazy';
    iframe.referrerPolicy = 'strict-origin-when-cross-origin';
    iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen';
    iframe.allowFullscreen = true;
    shell.replaceChildren(iframe);
  }, {once:true});
});
