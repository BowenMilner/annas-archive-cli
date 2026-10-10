// Port of src/anna/parsing.py. Run against the loaded WebKit DOM, never regex HTML.
(() => {
  const clean = node => {
    if (!node) return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('.select-none,script,style,.hidden').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  };
  const summary = node => {
    if (!node) return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('a,script,style').forEach(n => n.remove());
    return clean(copy).replace(/[ ·]+$/, '');
  };
  const icon = (node, name) => clean(node?.querySelector(`[class*="${name}"]`)?.closest('a'));
  const url = value => {
    try { const u = new URL(value, document.baseURI); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; }
    catch { return null; }
  };
  const metadata = value => {
    const parts = value.split(/[,·]/).map(v => v.trim());
    return {
      language: value.match(/\[([a-z]{2,3}(?:[-_][\w]+)?)\]/i)?.[1] || '',
      format: (parts.find(v => /^(pdf|epub|mobi|azw3?|djvu?|txt|rtf|docx?|cb[rz]|fb2|zip|html)$/i.test(v)) || '').toLowerCase(),
      size: value.match(/\b\d+(?:[.,]\d+)?\s*[KMGT]?i?B\b/i)?.[0] || ''
    };
  };
  const guard = () => {
    if (/ddos-guard|just a moment|attention required/i.test(document.title) ||
        document.querySelector('script[src*="ddos-guard"],script[src*="challenge-platform"],#challenge-form,#cf-challenge-running')) {
      throw new Error('browser_check');
    }
    if (/this domain may be for sale|forsale.min.js/i.test(document.documentElement.innerHTML)) throw new Error('parked_domain');
    document.querySelectorAll('.js-scroll-hidden').forEach(container => {
      const walk = document.createTreeWalker(container, NodeFilter.SHOW_COMMENT);
      const comments = []; while (walk.nextNode()) comments.push(walk.currentNode);
      comments.forEach(comment => {
        const template = document.createElement('template');
        template.innerHTML = comment.data;
        // Insert parsed cards only; do not activate scripts from hidden results.
        template.content.querySelectorAll('script,iframe,object,embed').forEach(n => n.remove());
        template.content.querySelectorAll('*').forEach(n => {
          Array.from(n.attributes).filter(a => /^on/i.test(a.name)).forEach(a => n.removeAttribute(a.name));
        });
        comment.replaceWith(template.content);
      });
    });
  };
  const recordPath = /^\/md5\/([0-9a-f]{32})\/?$/i;
  const makeBook = (md5, title, container, meta, author, publisher) => ({
    md5, title, url: new URL('/md5/' + md5, document.baseURI).href,
    author, publisher, ...metadata(meta), description: clean(container?.querySelector('.js-md5-top-box-description')), links: []
  });
  const search = () => {
    guard(); const books = new Map();
    document.querySelectorAll('a[href*="/md5/"]').forEach(anchor => {
      const resolved = url(anchor.getAttribute('href')); if (!resolved) return;
      const match = new URL(resolved).pathname.match(recordPath); if (!match) return;
      const md5 = match[1].toLowerCase(); if (books.has(md5)) return;
      let title = anchor.querySelector('h3,.font-bold');
      if (anchor.classList.contains('js-vim-focus') && !anchor.querySelector('h3')) {
        const details = anchor.parentElement?.parentElement;
        if (!details || !clean(anchor)) return;
        books.set(md5, makeBook(md5, clean(anchor), details, summary(details.querySelector('.text-gray-800')),
          icon(details, 'mdi--user-edit'), icon(details, 'mdi--company')));
      } else if (title && clean(title) && !anchor.querySelector('.js-aarecord-list-fallback-cover')) {
        const author = anchor.querySelector('.italic');
        books.set(md5, makeBook(md5, clean(title), anchor, clean(anchor.querySelector('.text-gray-500')),
          clean(author), title.nextElementSibling === author ? '' : clean(title.nextElementSibling)));
      }
    });
    if (!books.size && !/no files found|no results|没有找到|未找到|找不到/i.test(document.body.textContent)) throw new Error('unrecognized_search');
    return [...books.values()];
  };
  const info = md5 => {
    guard(); let title = document.querySelector('div.text-2xl.font-semibold');
    let book;
    if (title && clean(title)) {
      const container = title.parentElement;
      book = makeBook(md5, clean(title), container, summary(container.querySelector('.text-gray-800')),
        icon(container, 'mdi--user-edit'), icon(container, 'mdi--company'));
    } else {
      title = document.querySelector('.text-3xl');
      if (!title || !clean(title)) throw new Error('unrecognized_record');
      const publisher = title.nextElementSibling;
      book = makeBook(md5, clean(title), title.parentElement, clean(title.previousElementSibling),
        clean(publisher?.nextElementSibling), clean(publisher));
    }
    const seen = new Set();
    document.querySelectorAll('a.js-download-link[href]').forEach(a => {
      const resolved = url(a.getAttribute('href')); if (!resolved || seen.has(resolved)) return;
      seen.add(resolved); const path = new URL(resolved).pathname;
      const kind = path.includes('/fast_download/') ? 'fast' : path.includes('/slow_download/') ? 'slow' : 'external';
      book.links.push({label: clean(a) || new URL(resolved).hostname, url: resolved, kind});
    });
    return book;
  };
  window.bookfinder = {
    extract: (mode, md5) => {
      try { return JSON.stringify({value: mode === 'search' ? search() : info(md5)}); }
      catch (e) { return JSON.stringify({error: e.message}); }
    }
  };
})();
