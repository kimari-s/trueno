/* Shared helpers for index.html / thumbs.html. Served as a static file. */
(function (w) {
  "use strict";
  function fmtBytes(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(0) + " KB";
    return n + " B";
  }
  function fmtDuration(sec) {
    if (sec >= 86400) return Math.round(sec / 86400) + "d";
    if (sec >= 3600) return Math.round(sec / 3600) + "h";
    if (sec >= 60) return Math.round(sec / 60) + "m";
    return sec + "s";
  }
  function fmtDate(epoch) {
    var d = new Date(epoch * 1000);
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function fmtLeft(expires, now) {
    if (expires === null) return "keep";
    var left = expires - now;
    return left <= 0 ? "soon" : fmtDuration(left);
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // The original filename is shown beside the id unless the uploader hid it:
  // a bare "_" stem (e.g. "_.jpg") is the convention for "do not show my filename".
  function showName(it) {
    return it.name !== it.id && it.name.replace(/\.[^.]*$/, "") !== "_";
  }
  function pager(page, pages, onPage, total) {
    var el = document.createElement("div");
    el.className = "pages";
    var html = "";
    for (var i = 1; i <= pages; i++) {
      html += i === page ? "<b>[" + i + "]</b>" : '<a href="#" data-p="' + i + '">[' + i + "]</a>";
    }
    html += '<span class="n">' + total + " files</span>";
    el.innerHTML = html;
    el.addEventListener("click", function (ev) {
      var a = ev.target.closest("a[data-p]");
      if (!a) return;
      ev.preventDefault();
      onPage(parseInt(a.dataset.p, 10));
    });
    return el;
  }
  w.TruenoUI = { showName: showName, fmtBytes: fmtBytes, fmtDuration: fmtDuration, fmtDate: fmtDate, fmtLeft: fmtLeft, esc: esc, pager: pager,
    IMAGE_EXTS: /\.(jpe?g|png|gif|webp|avif|bmp|svg|ico)$/i };
})(window);
