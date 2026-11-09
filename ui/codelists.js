const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
const bar = document.querySelector('progress');
const progressText = document.querySelector('[data-progress-text]');
const openForm = () => {
  const address = new URL(location.href);
  address.searchParams.set('codelists', '1');
  location.replace(address);
};
const listsWord = count => count === 1 ? 'číselníku' : 'číselníků';
const follow = async () => {
  const progress = await fetch(`/codelists-progress?t=${pageData.token}`).then(response => response.ok ? response.json() : null).catch(() => null);
  if (!progress || progress.finished) return openForm();
  bar.max = progress.total;
  bar.value = progress.done;
  progressText.textContent = `${progress.done} z ${progress.total} ${listsWord(progress.total)}`;
  setTimeout(follow, 250);
};
document.querySelector('[data-skip]').addEventListener('click', openForm);
follow();
