#include "library_view.h"

#include <set>

const QString INSTALLED = u"installed"_s;

static const QStringList COLUMNS = {u"이름"_s, u"분류"_s, u"상태"_s, u"크기"_s, u"추가일"_s, u"위치"_s};

static ItemPtr itemAt(const QModelIndex& index) {
    return index.data(ItemRole).value<ItemPtr>();
}

// ---------------------------------------------------------------- ItemModel

void ItemModel::setItems(const QList<ItemPtr>& list) {
    beginResetModel();
    items = list;
    endResetModel();
}

int ItemModel::rowCount(const QModelIndex& parent) const {
    return parent.isValid() ? 0 : int(items.size());
}

int ItemModel::columnCount(const QModelIndex&) const {
    return int(COLUMNS.size());
}

QVariant ItemModel::headerData(int section, Qt::Orientation orientation, int role) const {
    if (orientation == Qt::Horizontal && role == Qt::DisplayRole)
        return COLUMNS.value(section);
    return {};
}

QVariant ItemModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid() || index.row() >= items.size())
        return {};
    const ItemPtr& ptr = items[index.row()];
    const Item& it = *ptr;
    const int col = index.column();
    if (role == ItemRole)
        return QVariant::fromValue(ptr);
    if (role == Qt::DisplayRole) {
        switch (col) {
        case 0: return it.name;
        case 1: return categoryPathLabel(it.category);
        case 2: return statusLabel(it.status);
        case 3: return humanSize(it.size);
        case 4: return pyDate(it.addedAt);
        case 5: return it.relPath;
        }
        return {};
    }
    if (role == Qt::UserRole) {
        switch (col) {
        case 0: return it.name.toLower();
        case 1: return categoryPathLabel(it.category);
        case 2: return it.status;
        case 3: return it.size;
        case 4: return it.addedAt;
        case 5: return it.relPath;
        }
        return {};
    }
    if (role == Qt::DecorationRole && col == 0)
        return itemIcon(it);
    if (role == Qt::CheckStateRole && col == 0)
        return it.status == u"active"_s ? Qt::Checked : Qt::Unchecked;
    if (role == Qt::ToolTipRole) {
        QString tip = it.name + u'\n' + categoryPathLabel(it.category) + u'\n' + it.reason;
        if (!it.memo.isEmpty())
            tip += u"\n\n메모: "_s + it.memo.left(200);
        return tip;
    }
    if (role == Qt::ForegroundRole && it.status != u"active"_s)
        return QColor(tok("faint"));
    return {};
}

Qt::ItemFlags ItemModel::flags(const QModelIndex& index) const {
    Qt::ItemFlags f = QAbstractTableModel::flags(index);
    if (index.isValid() && index.column() == 0 && items[index.row()]->status != u"quarantine"_s)
        f |= Qt::ItemIsUserCheckable;
    return f;
}

bool ItemModel::setData(const QModelIndex& index, const QVariant& value, int role) {
    if (role == Qt::CheckStateRole && index.column() == 0 && checkToggled)
        checkToggled(items[index.row()], value.toInt() == Qt::Checked);
    return false;
}

// ---------------------------------------------------------------- ItemFilter

ItemFilter::ItemFilter() {
    setSortRole(Qt::UserRole);
}

void ItemFilter::setScope(const QString& group, const QString& category, const QString& status) {
    group_ = group;
    category_ = category;
    status_ = status;
    invalidateFilter();
}

void ItemFilter::setText(const QString& text) {
    text_ = text.trimmed().toLower();
    invalidateFilter();
}

bool ItemFilter::filterAcceptsRow(int row, const QModelIndex&) const {
    const Item& it = *static_cast<ItemModel*>(sourceModel())->items[row];
    if (status_ == INSTALLED) {
        if (it.status != u"active"_s && it.status != u"disabled"_s)
            return false;
    } else if (!status_.isEmpty() && it.status != status_) {
        return false;
    }
    if (!category_.isEmpty() && it.category != category_)
        return false;
    if (!group_.isEmpty() && cats::groupOf(it.category) != group_)
        return false;
    if (!text_.isEmpty()) {
        const QString hay = (it.name + u' ' + it.memo + u' ' + it.relPath + u' ' + it.caspNames.join(u' ')).toLower();
        for (const QString& t : text_.split(QRegularExpression(u"\\s+"_s), Qt::SkipEmptyParts))
            if (!hay.contains(t))
                return false;
    }
    return true;
}

// ---------------------------------------------------------------- GridDelegate

QRect GridDelegate::imageRect(const Item& item, const QRect& rect) const {
    const QRect box(rect.x() + (rect.width() - icon) / 2, rect.y() + PAD, icon, icon);
    const QPixmap pm = itemPixmap(item, icon);
    const int w = int(pm.width() / pm.devicePixelRatio()), h = int(pm.height() / pm.devicePixelRatio());
    return QRect(box.x() + (icon - w) / 2, box.y() + (icon - h) / 2, w, h);
}

QRect GridDelegate::badgeRect(const Item& item, const QRect& rect) const {
    const QRect img = imageRect(item, rect);
    const int size = qMax(14, qMin(24, icon / 6));
    const int inset = qMax(3, icon / 22);
    return QRect(img.x() + inset, img.y() + inset, size, size);
}

void GridDelegate::paint(QPainter* p, const QStyleOptionViewItem& option, const QModelIndex& index) const {
    const ItemPtr ptr = itemAt(index);
    if (!ptr)
        return;
    const Item& item = *ptr;
    const QRect rect = option.rect.adjusted(2, 2, -2, -2);
    p->save();
    p->setRenderHint(QPainter::Antialiasing);
    p->setRenderHint(QPainter::SmoothPixmapTransform);
    if (option.state & QStyle::State_Selected) {
        p->setPen(Qt::NoPen);
        p->setBrush(QColor(tok("sel")));
        p->drawRoundedRect(QRectF(rect), 8, 8);
    } else if (option.state & QStyle::State_MouseOver) {
        p->setPen(Qt::NoPen);
        p->setBrush(QColor(tok("hover")));
        p->drawRoundedRect(QRectF(rect), 8, 8);
    }

    const bool active = item.status == u"active"_s;
    const QRect img = imageRect(item, rect);
    p->setOpacity(active ? 1.0 : 0.35);
    p->drawPixmap(img, itemPixmap(item, icon));
    p->setOpacity(1.0);

    if (!active) {
        const QString text = item.status == u"disabled"_s ? u"비활성"_s : u"테스트 중"_s;
        QFont f(option.font);
        f.setPointSizeF(8);
        p->setFont(f);
        const QFontMetrics fm = p->fontMetrics();
        const int w = fm.horizontalAdvance(text) + 12, h = fm.height() + 4;
        const QRectF chip(img.left() + 4, img.bottom() - h - 4, w, h);
        p->setPen(QPen(QColor(tok("border_strong")), 1));
        p->setBrush(QColor(tok("surface")));
        p->drawRoundedRect(chip, h / 2.0, h / 2.0);
        p->setPen(QColor(tok("muted")));
        p->drawText(chip, Qt::AlignCenter, text);
    }

    if (item.hq) {
        QFont f(option.font);
        f.setPointSizeF(qMax(6.5, qMin(9.0, icon / 18.0)));
        f.setBold(true);
        p->setFont(f);
        const QFontMetrics fm = p->fontMetrics();
        const int w = fm.horizontalAdvance(u"HQ"_s) + 8, h = fm.height() + 1;
        const int inset = qMax(3, icon / 22);
        const QRectF chip(img.right() - w - inset + 1, img.bottom() - h - inset + 1, w, h);
        p->setPen(Qt::NoPen);
        QColor bg(20, 20, 20);
        bg.setAlpha(190);
        p->setBrush(bg);
        p->drawRoundedRect(chip, 4, 4);
        p->setPen(QColor(u"#ffffff"_s));
        p->drawText(chip, Qt::AlignCenter, u"HQ"_s);
    }

    if (item.status != u"quarantine"_s) {
        const QRectF b(badgeRect(item, rect));
        if (active) {
            p->setPen(Qt::NoPen);
            p->setBrush(QColor(tok("accent")));
            p->drawEllipse(b);
            QPainterPath path(QPointF(b.x() + b.width() * 0.28, b.y() + b.height() * 0.53));
            path.lineTo(b.x() + b.width() * 0.44, b.y() + b.height() * 0.69);
            path.lineTo(b.x() + b.width() * 0.73, b.y() + b.height() * 0.36);
            QPen pen(QColor(tok("on_accent")), 2.2);
            pen.setCapStyle(Qt::RoundCap);
            pen.setJoinStyle(Qt::RoundJoin);
            p->setPen(pen);
            p->setBrush(Qt::NoBrush);
            p->drawPath(path);
        } else {
            QColor bg(tok("window"));
            bg.setAlpha(215);
            p->setBrush(bg);
            p->setPen(QPen(QColor(tok("muted")), 1.6));
            p->drawEllipse(b.adjusted(0.8, 0.8, -0.8, -0.8));
        }
    }

    const QRect nameRect(rect.x() + 6, rect.y() + PAD + icon + 6, rect.width() - 12, 20);
    p->setFont(option.font);
    p->setPen(QColor(active ? tok("text") : tok("faint")));
    p->drawText(nameRect, Qt::AlignHCenter | Qt::AlignVCenter,
                p->fontMetrics().elidedText(item.name, Qt::ElideRight, nameRect.width()));
    p->restore();
}

bool GridDelegate::editorEvent(QEvent* event, QAbstractItemModel* model, const QStyleOptionViewItem& option, const QModelIndex& index) {
    const auto type = event->type();
    if (type != QEvent::MouseButtonPress && type != QEvent::MouseButtonRelease && type != QEvent::MouseButtonDblClick)
        return false;
    const ItemPtr item = itemAt(index);
    auto* me = static_cast<QMouseEvent*>(event);
    if (!item || item->status == u"quarantine"_s || me->button() != Qt::LeftButton)
        return false;
    const QRect rect = option.rect.adjusted(2, 2, -2, -2);
    if (!badgeRect(*item, rect).adjusted(-3, -3, 3, 3).contains(me->position().toPoint()))
        return false;
    if (type == QEvent::MouseButtonRelease)
        model->setData(index, item->status == u"active"_s ? Qt::Unchecked : Qt::Checked, Qt::CheckStateRole);
    return true;
}

// ---------------------------------------------------------------- ElideLabel / ThumbLabel

ElideLabel::ElideLabel(Qt::TextElideMode mode) : mode_(mode) {
    setMinimumWidth(40);
    setTextInteractionFlags(Qt::TextSelectableByMouse);
}

void ElideLabel::setFull(const QString& text) {
    full_ = text;
    setToolTip(text);
    elide();
}

void ElideLabel::resizeEvent(QResizeEvent* e) {
    QLabel::resizeEvent(e);
    elide();
}

void ElideLabel::elide() {
    QLabel::setText(fontMetrics().elidedText(full_, mode_, qMax(10, width())));
}

QSize ElideLabel::minimumSizeHint() const {
    return QSize(40, QLabel::minimumSizeHint().height());
}

ThumbLabel::ThumbLabel() {
    setFixedSize(220, 220);
    setAlignment(Qt::AlignCenter);
    setAcceptDrops(true);
    setFocusPolicy(Qt::ClickFocus);
    setToolTip(u"이미지를 끌어다 놓으면 미리보기를 바꿀 수 있습니다."_s);
    // 포즈 팩처럼 이미지가 여러 장일 때만 보이는 좌우 화살표
    prev = new QToolButton(this);
    prev->setObjectName(u"thumbPrev"_s);
    prev->setToolTip(u"이전 이미지"_s);
    next = new QToolButton(this);
    next->setObjectName(u"thumbNext"_s);
    next->setToolTip(u"다음 이미지"_s);
    for (QToolButton* b : {prev, next}) {
        b->setFixedSize(28, 48);
        b->setIconSize(QSize(16, 16));
        b->setCursor(Qt::PointingHandCursor);
        b->hide();
    }
    prev->move(0, (220 - 48) / 2);
    next->move(220 - 28, (220 - 48) / 2);
    connect(prev, &QToolButton::clicked, this, [this] {
        if (step)
            step(-1);
    });
    connect(next, &QToolButton::clicked, this, [this] {
        if (step)
            step(1);
    });
}

void ThumbLabel::setNavVisible(bool on) {
    prev->setVisible(on);
    next->setVisible(on);
}

void ThumbLabel::wheelEvent(QWheelEvent* e) {
    if (prev->isVisible() && step && e->angleDelta().y() != 0) {
        step(e->angleDelta().y() > 0 ? -1 : 1);
        e->accept();
        return;
    }
    QLabel::wheelEvent(e);
}

void ThumbLabel::keyPressEvent(QKeyEvent* e) {
    if (prev->isVisible() && step && (e->key() == Qt::Key_Left || e->key() == Qt::Key_Right)) {
        step(e->key() == Qt::Key_Left ? -1 : 1);
        e->accept();
        return;
    }
    QLabel::keyPressEvent(e);
}

void ThumbLabel::dragEnterEvent(QDragEnterEvent* e) {
    if (e->mimeData()->hasUrls())
        e->acceptProposedAction();
}

void ThumbLabel::dropEvent(QDropEvent* e) {
    const auto urls = e->mimeData()->urls();
    if (!urls.isEmpty() && dropped)
        dropped(urls.first().toLocalFile());
}

// ---------------------------------------------------------------- DetailPanel

void DetailPanel::emitRequest(const char* action) {
    if (request)
        request(QString::fromLatin1(action));
}

DetailPanel::DetailPanel() {
    setObjectName(u"card"_s);
    setMinimumWidth(300);
    setMaximumWidth(380);
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(14, 14, 14, 14);
    lay->setSpacing(8);

    thumb = new ThumbLabel;
    thumb->dropped = [this](const QString& path) { setThumbFile(path); };
    thumb->step = [this](int d) {
        if (poseImages.size() < 2)
            return;
        const int n = int(poseImages.size());
        poseIndex = ((poseIndex + d) % n + n) % n;
        showPose();
    };
    lay->addWidget(thumb, 0, Qt::AlignHCenter);
    poseCount = new QLabel;
    poseCount->setObjectName(u"muted"_s);
    poseCount->setAlignment(Qt::AlignCenter);
    poseCount->hide();
    lay->addWidget(poseCount, 0, Qt::AlignHCenter);
    auto* row = new QHBoxLayout;
    auto* bImg = new QPushButton(u"이미지 변경…"_s);
    connect(bImg, &QPushButton::clicked, this, [this] { pickThumb(); });
    auto* bPaste = new QPushButton(u"붙여넣기"_s);
    bPaste->setToolTip(u"클립보드의 이미지로 미리보기 지정"_s);
    connect(bPaste, &QPushButton::clicked, this, [this] { pasteThumb(); });
    row->addWidget(bImg);
    row->addWidget(bPaste);
    lay->addLayout(row);

    title = new QLabel;
    title->setObjectName(u"h1"_s);
    title->setWordWrap(true);
    lay->addWidget(title);
    warn = new QLabel;
    warn->setObjectName(u"warn"_s);
    warn->setWordWrap(true);
    lay->addWidget(warn);

    auto* form = new QFormLayout;
    cat = new CategoryCombo;
    connect(cat, &QComboBox::activated, this, [this] { categoryChanged(); });
    reason = new QLabel;
    reason->setObjectName(u"muted"_s);
    reason->setWordWrap(true);
    info = new QLabel;
    info->setObjectName(u"muted"_s);
    info->setWordWrap(true);
    memo = new QPlainTextEdit;
    memo->setPlaceholderText(u"메모를 입력하세요"_s);
    form->addRow(u"분류"_s, cat);
    form->addRow(QString(), reason);
    lay->addLayout(form);
    lay->addWidget(info);
    facts = new QWidget;
    auto* grid = new QGridLayout(facts);
    grid->setContentsMargins(0, 4, 0, 4);
    grid->setHorizontalSpacing(12);
    grid->setVerticalSpacing(5);
    grid->setColumnStretch(1, 1);
    const QList<std::pair<QString, QString>> rows = {
        {u"status"_s, u"상태"_s}, {u"size"_s, u"크기"_s}, {u"quality"_s, u"화질"_s},
        {u"folder"_s, u"위치"_s}, {u"inner"_s, u"내부 이름"_s}, {u"files"_s, u"구성 파일"_s}};
    int i = 0;
    for (const auto& [key, label] : rows) {
        auto* k = new QLabel(label);
        k->setObjectName(u"muted"_s);
        auto* v = new ElideLabel;
        grid->addWidget(k, i, 0, Qt::AlignLeft | Qt::AlignTop);
        grid->addWidget(v, i, 1);
        factRows.append({key, {k, v}});
        ++i;
    }
    lay->addWidget(facts);
    lay->addWidget(new QLabel(u"메모"_s));
    lay->addWidget(memo, 1);

    saveTimer = new QTimer(this);
    saveTimer->setSingleShot(true);
    saveTimer->setInterval(600);
    connect(saveTimer, &QTimer::timeout, this, [this] { saveNote(); });
    connect(memo, &QPlainTextEdit::textChanged, this, [this] {
        if (memo->hasFocus())
            saveTimer->start();
    });

    auto* btns = new QHBoxLayout;
    bOpen = new QPushButton(u"폴더 열기"_s);
    connect(bOpen, &QPushButton::clicked, this, [this] { emitRequest("reveal"); });
    bOn = new QPushButton(u"활성화"_s);
    bOn->setToolTip(u"Mods 폴더로 되돌려 게임에서 쓰이게 합니다."_s);
    connect(bOn, &QPushButton::clicked, this, [this] { emitRequest("enable"); });
    bOff = new QPushButton(u"비활성화"_s);
    bOff->setToolTip(u"Mods 밖(S4CCM_Disabled)으로 옮겨 게임에서 빠지게 합니다. 파일은 지워지지 않습니다."_s);
    connect(bOff, &QPushButton::clicked, this, [this] { emitRequest("disable"); });
    bDel = new QPushButton(u"휴지통으로"_s);
    bDel->setObjectName(u"danger"_s);
    connect(bDel, &QPushButton::clicked, this, [this] { emitRequest("trash"); });
    for (auto* b : {bOpen, bOn, bOff, bDel})
        btns->addWidget(b);
    lay->addLayout(btns);
    showItems({});
}

void DetailPanel::showItems(const QList<ItemPtr>& list) {
    if (saveTimer->isActive()) {
        saveTimer->stop();
        saveNote();
    }
    items = list;
    const ItemPtr one = items.size() == 1 ? items.first() : nullptr;
    ++poseGen;
    poseImages.clear();
    thumb->setNavVisible(false);
    poseCount->hide();
    for (QWidget* w : std::initializer_list<QWidget*>{cat, memo, bOpen, bOn, bOff, bDel})
        w->setEnabled(!items.isEmpty());
    for (QWidget* w : std::initializer_list<QWidget*>{memo, bOpen})
        w->setEnabled(one != nullptr);
    memo->blockSignals(true);
    if (items.isEmpty()) {
        thumb->setPixmap(placeholder(QString(), 200));
        title->setText(u"CC를 선택하세요"_s);
        reason->clear();
        info->setText(u"목록에서 CC를 선택하면 자세한 정보가 표시됩니다.\n이름 앞 체크를 끄면 비활성화, 켜면 활성화됩니다.\n파일이나 압축파일을 창에 끌어다 놓으면 추가됩니다."_s);
        showFacts({});
        warn->hide();
        memo->clear();
        cat->setCurrentIndex(-1);
    } else if (one) {
        const QPixmap pm = (!one->displayThumb.isEmpty() && QFileInfo::exists(one->displayThumb))
                               ? QPixmap(one->displayThumb)
                               : placeholder(cats::groupOf(one->category), 200);
        thumb->setPixmap(pm.scaled(210, 210, Qt::KeepAspectRatio, Qt::SmoothTransformation));
        if (one->kind == u"package"_s && one->category == u"animations"_s) {
            const int gen = poseGen;
            const qint64 id = one->id;
            const QString userThumb = one->userThumb;
            auto found = std::make_shared<QStringList>();
            poseTask.start(
                this,
                [id, found] {
                    try {
                        for (const auto& v : coreCall("lib.pose_images", {{"id"_L1, id}}).toArray())
                            *found << v.toString();
                    } catch (const CoreError&) {
                    }
                },
                [this, gen, found, userThumb] {
                    poseTask.join();
                    if (gen != poseGen)
                        return;   // 그사이 다른 항목을 골랐다
                    QStringList all = *found;
                    if (!userThumb.isEmpty() && QFileInfo::exists(userThumb))
                        all.prepend(userThumb);   // 직접 지정한 미리보기가 있으면 맨 앞에
                    if (all.size() < 2)
                        return;
                    poseImages = all;
                    poseIndex = 0;
                    thumb->setNavVisible(true);
                    poseCount->show();
                    showPose();
                });
        }
        title->setText(one->name);
        const bool package = one->kind == u"package"_s;
        cat->setEnabled(package);
        if (package)
            cat->setKey(one->category);
        else
            cat->setCurrentIndex(-1);
        const QString src = one->userSet ? u"직접 지정"_s : u"자동 판정 (신뢰도 %1)"_s.arg(pyPercent(one->confidence));
        reason->setText(package ? src + u" · "_s + one->reason : one->reason);
        QStringList names;
        for (const QString& n : one->caspNames)
            if (!n.trimmed().isEmpty())
                names << n.trimmed();
        info->clear();
        const QString tail = rsplitTail(one->relPath);
        QHash<QString, QString> values;
        values[u"status"_s] = statusLabel(one->status);
        values[u"size"_s] = humanSize(one->size);
        values[u"quality"_s] = one->hasTex
            ? (one->hq ? u"HQ 고화질"_s : u"일반"_s) + u" · %1×%2"_s.arg(one->texW).arg(one->texH)
            : QString();
        values[u"folder"_s] = (package || tail.contains(u'.')) ? rsplitHead(one->relPath) : one->relPath;
        values[u"inner"_s] = names.isEmpty() ? QString() : names.first();
        values[u"files"_s] = one->kind == u"script"_s ? u"%1개"_s.arg(one->members) : QString();
        showFacts(values, {{u"folder"_s, one->relPath}});
        warn->setVisible(!one->warning.isEmpty());
        warn->setText(one->warning);
        memo->setPlainText(one->memo);
    } else {
        thumb->setPixmap(placeholder(QString(), 200));
        title->setText(u"%1개 선택됨"_s.arg(items.size()));
        bool allPackages = true;
        qint64 total = 0;
        for (const auto& it : items) {
            allPackages = allPackages && it->kind == u"package"_s;
            total += it->size;
        }
        cat->setEnabled(allPackages);
        cat->setCurrentIndex(-1);
        reason->setText(u"분류를 고르면 선택한 CC가 모두 해당 폴더로 이동합니다."_s);
        info->clear();
        showFacts({{u"size"_s, humanSize(total)}});
        warn->hide();
        memo->clear();
    }
    memo->blockSignals(false);
    if (!items.isEmpty()) {
        bool anyDisabled = false, anyActive = false;
        for (const auto& it : items) {
            anyDisabled = anyDisabled || it->status == u"disabled"_s;
            anyActive = anyActive || it->status == u"active"_s;
        }
        bOn->setEnabled(anyDisabled);
        bOff->setEnabled(anyActive);
    }
}

void DetailPanel::showPose() {
    const QPixmap pm(poseImages.value(poseIndex));
    if (!pm.isNull())
        thumb->setPixmap(pm.scaled(210, 210, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    poseCount->setText(u"%1 / %2"_s.arg(poseIndex + 1).arg(poseImages.size()));
}

void DetailPanel::showFacts(const QHash<QString, QString>& values, const QHash<QString, QString>& tooltip) {
    bool any = false;
    for (const QString& v : values)
        any = any || !v.isEmpty();
    facts->setVisible(any);
    info->setVisible(!info->text().isEmpty());
    for (const auto& [key, pair] : factRows) {
        const QString text = values.value(key);
        pair.first->setVisible(!text.isEmpty());
        pair.second->setVisible(!text.isEmpty());
        pair.second->setFull(text);
        if (tooltip.contains(key))
            pair.second->setToolTip(tooltip.value(key));
    }
}

void DetailPanel::saveNote() {
    if (items.size() != 1)
        return;
    const ItemPtr it = items.first();
    it->memo = memo->toPlainText();
    coreCall("lib.save_note", {{"id"_L1, it->id}, {"memo"_L1, it->memo}});
}

void DetailPanel::categoryChanged() {
    const QString key = cat->key();
    if (key.isEmpty() || items.isEmpty())
        return;
    for (const auto& it : items)
        if (it->kind == u"package"_s && it->category != key)
            fillItem(*it, coreCall("lib.set_category", {{"id"_L1, it->id}, {"key"_L1, key}}).toObject());
    if (changed)
        changed();
}

void DetailPanel::setThumbFile(const QString& path) {
    if (items.size() == 1 && !path.isEmpty()) {
        forgetIcon(items.first()->userThumb);
        const QString res = coreCall("lib.set_user_thumb", {{"id"_L1, items.first()->id}, {"path"_L1, path}}).toString();
        if (!res.isEmpty()) {
            items.first()->userThumb = res;
            items.first()->displayThumb = res;
            forgetIcon(res);
            showItems(items);
            if (changed)
                changed();
        }
    }
}

void DetailPanel::pickThumb() {
    const QString path = QFileDialog::getOpenFileName(this, u"미리보기 이미지 선택"_s, QString(), u"이미지 (*.png *.jpg *.jpeg *.webp *.bmp)"_s);
    setThumbFile(path);
}

void DetailPanel::pasteThumb() {
    const QImage img = QGuiApplication::clipboard()->image();
    if (img.isNull() || items.size() != 1)
        return;
    QBuffer buf;
    buf.open(QIODevice::WriteOnly);
    img.save(&buf, "PNG");
    forgetIcon(items.first()->userThumb);
    const QString res = coreCall("lib.set_user_thumb", {{"id"_L1, items.first()->id}, {"hex"_L1, QString::fromLatin1(buf.data().toHex())}}).toString();
    if (!res.isEmpty()) {
        items.first()->userThumb = res;
        items.first()->displayThumb = res;
        forgetIcon(res);
        showItems(items);
        if (changed)
            changed();
    }
}

// ---------------------------------------------------------------- LibraryPage

static const QList<int> THUMB_SIZES = {64, 80, 96, 112, 128, 160, 192, 224, 256};

LibraryPage::LibraryPage() {
    model = new ItemModel;
    model->setParent(this);
    proxy = new ItemFilter;
    proxy->setParent(this);
    proxy->setSourceModel(model);
    proxy->sort(0);
    model->checkToggled = [this](const ItemPtr& item, bool on) {
        const QList<ItemPtr> sel = selected();
        bool inSel = false;
        for (const auto& i : sel)
            inSel = inSel || i->id == item->id;
        if (statusRequested)
            statusRequested(inSel ? sel : QList<ItemPtr>{item}, on ? u"active"_s : u"disabled"_s);
    };

    grid = new QListView;
    grid->setObjectName(u"grid"_s);
    grid->setSpacing(4);
    grid->setViewMode(QListView::IconMode);
    gridDelegate = new GridDelegate(grid, savedThumbSize());
    grid->setItemDelegate(gridDelegate);
    applyThumbSize();
    grid->viewport()->installEventFilter(this);
    grid->setMouseTracking(true);
    grid->setResizeMode(QListView::Adjust);
    grid->setMovement(QListView::Static);
    grid->setUniformItemSizes(true);
    grid->setWordWrap(true);
    grid->setTextElideMode(Qt::ElideRight);
    grid->setSelectionMode(QAbstractItemView::ExtendedSelection);
    grid->setModel(proxy);

    table = new QTableView;
    table->setModel(proxy);
    table->setSortingEnabled(true);
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->setSelectionMode(QAbstractItemView::ExtendedSelection);
    table->verticalHeader()->hide();
    table->setAlternatingRowColors(true);
    table->setShowGrid(false);
    QHeaderView* hdr = table->horizontalHeader();
    hdr->setSectionResizeMode(QHeaderView::Interactive);
    hdr->setStretchLastSection(true);
    int col = 0;
    for (int width : {320, 170, 90, 80, 90})
        table->setColumnWidth(col++, width);
    table->setIconSize(QSize(32, 32));
    table->verticalHeader()->setDefaultSectionSize(40);
    table->sortByColumn(0, Qt::AscendingOrder);

    views = new QStackedWidget;
    views->addWidget(grid);
    views->addWidget(table);

    detail = new DetailPanel;
    detail->changed = [this] { reload(); };

    for (QAbstractItemView* v : std::initializer_list<QAbstractItemView*>{grid, table}) {
        connect(v->selectionModel(), &QItemSelectionModel::selectionChanged, this, [this] { selectionChanged(); });
        v->setContextMenuPolicy(Qt::CustomContextMenu);
        connect(v, &QWidget::customContextMenuRequested, this, [this](const QPoint& pos) { contextMenu(pos); });
        connect(v, &QAbstractItemView::doubleClicked, this, [this] {
            if (detail->request)
                detail->request(u"reveal"_s);
        });
    }

    header = new QLabel;
    header->setObjectName(u"h1"_s);
    auto* center = new QWidget;
    auto* cl = new QVBoxLayout(center);
    cl->setContentsMargins(0, 0, 0, 0);
    cl->addWidget(header);
    cl->addWidget(views);

    // 그리드 아래 오른쪽: 썸네일 크기  ▫ ━━●━━ ▣
    zoomBar = new QWidget;
    auto* zl = new QHBoxLayout(zoomBar);
    zl->setContentsMargins(0, 4, 4, 0);
    zl->setSpacing(6);
    zl->addStretch(1);
    auto* zoomLabel = new QLabel(u"썸네일 크기"_s);
    zoomLabel->setObjectName(u"muted"_s);
    zl->addWidget(zoomLabel);
    auto* zoomOut = new QToolButton;
    zoomOut->setObjectName(u"zoomSmall"_s);
    zoomOut->setToolTip(u"썸네일 작게"_s);
    zoomOut->setAutoRaise(true);
    connect(zoomOut, &QToolButton::clicked, this, [this] { zoom(-1); });
    zoomSlider = new QSlider(Qt::Horizontal);
    zoomSlider->setRange(0, int(THUMB_SIZES.size()) - 1);
    zoomSlider->setPageStep(1);
    zoomSlider->setFixedWidth(160);
    zoomSlider->setFocusPolicy(Qt::NoFocus);
    zoomSlider->setValue(int(THUMB_SIZES.indexOf(gridDelegate->icon)));
    zoomSlider->setToolTip(u"Ctrl + 마우스 휠로도 조절할 수 있습니다"_s);
    connect(zoomSlider, &QSlider::valueChanged, this, [this](int i) { setThumbSize(THUMB_SIZES.value(i, 128)); });
    auto* zoomIn = new QToolButton;
    zoomIn->setObjectName(u"zoomLarge"_s);
    zoomIn->setToolTip(u"썸네일 크게"_s);
    zoomIn->setAutoRaise(true);
    connect(zoomIn, &QToolButton::clicked, this, [this] { zoom(1); });
    zl->addWidget(zoomOut);
    zl->addWidget(zoomSlider);
    zl->addWidget(zoomIn);
    cl->addWidget(zoomBar);

    auto* split = new QSplitter;
    split->addWidget(center);
    split->addWidget(detail);
    split->setStretchFactor(0, 1);
    auto* lay = new QHBoxLayout(this);
    lay->setContentsMargins(16, 12, 12, 12);
    lay->addWidget(split);
}

int LibraryPage::savedThumbSize() const {
    const int size = cfg().thumbSize;
    int best = THUMB_SIZES.first();
    for (int s : THUMB_SIZES)
        if (qAbs(s - size) < qAbs(best - size))
            best = s;
    return best;
}

void LibraryPage::applyThumbSize() {
    const int size = gridDelegate->icon;
    grid->setIconSize(QSize(size, size));
    grid->setGridSize(gridDelegate->cell() + QSize(4, 4));
    grid->doItemsLayout();
    grid->viewport()->update();
}

void LibraryPage::zoom(int steps) {
    const int i = int(THUMB_SIZES.indexOf(gridDelegate->icon));
    setThumbSize(THUMB_SIZES[qMax(0, qMin(int(THUMB_SIZES.size()) - 1, i + steps))]);
}

void LibraryPage::setThumbSize(int size) {
    if (size == gridDelegate->icon)
        return;
    {
        const QSignalBlocker block(zoomSlider);
        zoomSlider->setValue(int(THUMB_SIZES.indexOf(size)));
    }
    const QModelIndex current = grid->currentIndex();
    gridDelegate->icon = size;
    applyThumbSize();
    if (current.isValid())
        grid->scrollTo(current);
    setConfig({{"thumb_size"_L1, size}}, true);
}

bool LibraryPage::eventFilter(QObject* obj, QEvent* e) {
    if (obj == grid->viewport() && e->type() == QEvent::Wheel) {
        auto* we = static_cast<QWheelEvent*>(e);
        if (we->modifiers() & Qt::ControlModifier) {
            const int dy = we->angleDelta().y();
            if (dy)
                zoom(dy > 0 ? 1 : -1);
            return true;
        }
    }
    return QWidget::eventFilter(obj, e);
}

QAbstractItemView* LibraryPage::currentView() const {
    return static_cast<QAbstractItemView*>(views->currentWidget());
}

void LibraryPage::setViewMode(const QString& mode) {
    views->setCurrentWidget(mode == u"grid"_s ? static_cast<QWidget*>(grid) : table);
    zoomBar->setVisible(mode == u"grid"_s);   // 목록 보기는 썸네일이 32px 로 고정이라 숨긴다
    selectionChanged();
}

QList<ItemPtr> LibraryPage::selected() const {
    std::set<int> rows;
    for (const QModelIndex& i : currentView()->selectionModel()->selectedIndexes())
        rows.insert(proxy->mapToSource(i).row());
    QList<ItemPtr> out;
    for (int r : rows)
        if (r >= 0 && r < model->items.size())
            out << model->items[r];
    return out;
}

void LibraryPage::selectionChanged() {
    detail->showItems(selected());
}

void LibraryPage::setScope(const QString& title, const QString& group, const QString& category, const QString& status) {
    proxy->setScope(group, category, status);
    header->setText(u"%1  (%2)"_s.arg(title).arg(proxy->rowCount()));
    currentView()->clearSelection();
    title_ = title;
}

void LibraryPage::setSearch(const QString& text) {
    proxy->setText(text);
    header->setText(u"%1  (%2)"_s.arg(title_).arg(proxy->rowCount()));
}

void LibraryPage::reload() {
    QSet<qint64> sel;
    for (const auto& i : selected())
        sel.insert(i->id);
    model->setItems(itemsFrom(coreCall("lib.items")));
    header->setText(u"%1  (%2)"_s.arg(title_).arg(proxy->rowCount()));
    QAbstractItemView* v = currentView();
    if (!sel.isEmpty()) {
        for (int row = 0; row < model->items.size(); ++row) {
            if (sel.contains(model->items[row]->id)) {
                const QModelIndex idx = proxy->mapFromSource(model->index(row, 0));
                if (idx.isValid())
                    v->selectionModel()->select(idx, QItemSelectionModel::Select | QItemSelectionModel::Rows);
            }
        }
    }
    selectionChanged();
    if (countsChanged)
        countsChanged();
}

void LibraryPage::contextMenu(const QPoint& pos) {
    const QList<ItemPtr> items = selected();
    if (items.isEmpty())
        return;
    bool anyDisabled = false, anyActive = false, allPackages = true;
    for (const auto& i : items) {
        anyDisabled = anyDisabled || i->status == u"disabled"_s;
        anyActive = anyActive || i->status == u"active"_s;
        allPackages = allPackages && i->kind == u"package"_s;
    }
    auto req = [this](const char* action) {
        return [this, action] {
            if (detail->request)
                detail->request(QString::fromLatin1(action));
        };
    };
    QMenu m(this);
    m.addAction(u"폴더에서 보기"_s, this, req("reveal"))->setEnabled(items.size() == 1);
    m.addAction(u"활성화"_s, this, req("enable"))->setEnabled(anyDisabled);
    m.addAction(u"비활성화 (Mods 밖으로)"_s, this, req("disable"))->setEnabled(anyActive);
    if (allPackages) {
        QMenu* sub = m.addMenu(u"분류 변경"_s);
        for (const auto& [g, glabel] : cats::GROUPS) {
            if (g == cats::SCRIPT_MOD)
                continue;
            QList<cats::Category> cs;
            for (const auto& c : cats::CATEGORIES)
                if (c.group == g)
                    cs << c;
            QMenu* target = cs.size() > 1 ? sub->addMenu(glabel) : sub;
            for (const auto& c : cs) {
                const QString key = c.key;
                target->addAction(c.label, this, [this, items, key] { setCategory(items, key); });
            }
        }
    }
    m.addSeparator();
    m.addAction(u"휴지통으로 삭제"_s, this, req("trash"));
    m.exec(currentView()->viewport()->mapToGlobal(pos));
}

void LibraryPage::setCategory(const QList<ItemPtr>& list, const QString& key) {
    for (const auto& it : list)
        fillItem(*it, coreCall("lib.set_category", {{"id"_L1, it->id}, {"key"_L1, key}}).toObject());
    reload();
}
