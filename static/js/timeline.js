// 再生コントローラー: 日タブ + ステップスライダー + 再生/速度。

export class Timeline {
  constructor({ tabsEl, sliderEl, playBtn, speedSel, labelEl, onChange }) {
    this.tabsEl = tabsEl;
    this.sliderEl = sliderEl;
    this.playBtn = playBtn;
    this.speedSel = speedSel;
    this.labelEl = labelEl;
    this.onChange = onChange;

    this.days = [];
    this.day = 0;
    this.step = 0;
    this.timer = null;

    sliderEl.addEventListener('input', () => {
      this.step = Number(sliderEl.value);
      this._sync();
      this._emit();
    });
    playBtn.addEventListener('click', () => (this.timer ? this.pause() : this.play()));
    speedSel.addEventListener('change', () => {
      if (this.timer) {
        this.pause();
        this.play();
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.key === ' ') {
        e.preventDefault();
        this.timer ? this.pause() : this.play();
      } else if (e.key === 'ArrowRight') {
        this._advance();
      } else if (e.key === 'ArrowLeft') {
        this._back();
      }
    });
  }

  setMatch(days) {
    this.days = days;
    this.pause();
    this.day = 0;
    this.step = 0;
    this._buildTabs();
    this.sliderEl.disabled = false;
    this._sync();
    this._emit();
  }

  _buildTabs() {
    this.tabsEl.innerHTML = '';
    this.days.forEach((d, i) => {
      const btn = document.createElement('button');
      btn.className = 'day-tab';
      btn.textContent = `${i + 1}日目`;
      btn.addEventListener('click', () => this.goTo(i, 0));
      this.tabsEl.appendChild(btn);
    });
  }

  goTo(day, step) {
    this.day = day;
    this.step = step;
    this._sync();
    this._emit();
  }

  play() {
    if (!this.days.length) return;
    // 最終フレームで再生を押したら最初に戻す
    if (
      this.day === this.days.length - 1 &&
      this.step >= this.days[this.day].steps
    ) {
      this.day = 0;
      this.step = 0;
    }
    this.playBtn.textContent = '⏸';
    const interval = Number(this.speedSel.value);
    this.timer = setInterval(() => this._advance(true), interval);
  }

  pause() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.playBtn.textContent = '▶';
  }

  _advance(fromTimer = false) {
    if (!this.days.length) return;
    if (this.step < this.days[this.day].steps) {
      this.step += 1;
    } else if (this.day < this.days.length - 1) {
      this.day += 1;
      this.step = 0;
    } else {
      if (fromTimer) this.pause();
      return;
    }
    this._sync();
    this._emit();
  }

  _back() {
    if (!this.days.length) return;
    if (this.step > 0) {
      this.step -= 1;
    } else if (this.day > 0) {
      this.day -= 1;
      this.step = this.days[this.day].steps;
    } else {
      return;
    }
    this._sync();
    this._emit();
  }

  _sync() {
    const day = this.days[this.day];
    this.sliderEl.max = day ? day.steps : 0;
    this.sliderEl.value = this.step;
    this.labelEl.textContent = day
      ? `${this.day + 1}日目 / ステップ ${this.step} / ${day.steps}`
      : '- / -';
    [...this.tabsEl.children].forEach((tab, i) =>
      tab.classList.toggle('active', i === this.day)
    );
  }

  _emit() {
    this.onChange(this.day, this.step);
  }
}
