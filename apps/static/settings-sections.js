document.querySelectorAll('[data-section]').forEach(button => {
  button.onclick = () => {
    document.querySelectorAll('[data-section]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    document.getElementById('algorithm-settings').hidden = button.dataset.section !== 'algorithm';
    document.getElementById('turnover-settings').hidden = button.dataset.section !== 'turnover';
  };
});
