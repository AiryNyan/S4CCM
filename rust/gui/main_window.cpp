#include "main_window.h"

#include "dialogs.h"
#include "library_view.h"
#include "pages.h"
#include "symlink_page.h"
#include "tray_page.h"

#define NOMINMAX
#include <windows.h>
#include <shellapi.h>

static const QString ADD_FILTER =
    u"모드/트레이 파일 (*.package *.ts4script *.zip *.7z *.rar *.trayitem *.householdbinary *.blueprint "
    u"*.room *.hhi *.sgi *.bpi *.rmi);;모든 파일 (*)"_s;

MainWindow::MainWindow() {
    setWindowTitle(u"S4CCM"_s);
    resize(1400, 860);
    setAcceptDrops(true);
    lastScan_.start();

    auto* tb = new QToolBar;
    tb->setObjectName(u"main"_s);
    tb->setMovable(false);
    tb->setToolButtonStyle(Qt::ToolButtonTextOnly);
    addToolBar(tb);
    QAction* add = act(tb, u"CC 추가"_s, [this] { addFiles(); }, QKeySequence::Open, u"파일이나 압축파일(.zip/.7z/.rar)로 모드 추가"_s);
    tb->widgetForAction(add)->setObjectName(u"accent"_s);
    act(tb, u"폴더 추가"_s, [this] { addFolder(); }, {}, u"압축을 푼 모드 폴더 추가"_s);
    tb->addSeparator();
    act(tb, u"다시 스캔"_s, [this] { rescan(true); }, QKeySequence::Refresh);
    act(tb, u"자동 정리"_s, [this] { goTo(u"organize"_s); });
    auto* spacer = new QWidget;
    spacer->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
    tb->addWidget(spacer);
    search = new QLineEdit;
    search->setPlaceholderText(u"이름, 메모 검색"_s);
    search->setClearButtonEnabled(true);
    search->setFixedWidth(260);
    connect(search, &QLineEdit::textChanged, this, [this](const QString& text) { onSearch(text); });
    tb->addWidget(search);
    viewAct = act(tb, u"목록 보기"_s, [this] { toggleView(); });

    library = new LibraryPage;
    library->detail->request = [this](const QString& action) { detailAction(action); };
    library->statusRequested = [this](const QList<ItemPtr>& items, const QString& to) { setStatus(items, to); };
    library->countsChanged = [this] { updateCounts(); };
    organize = new OrganizePage;
    organize->changed = [this] { library->reload(); };
    cfgpage = new ResourceCfgPage;
    bisect = new BisectPage;
    bisect->changed = [this] { library->reload(); };
    tray = new TrayPage;
    dupes = new DuplicatesPage;
    symlink = new SymlinkPage;
    symlink->changed = [this] { library->reload(); };
    symlink->requestElevation = [this](const QString& dest) { elevateRelocate(dest); };
    dupes->changed = [this] { library->reload(); };
    settings = new SettingsPage;
    settings->simsDirChanged = [this] { simsDirChanged(); };
    settings->themeChanged = [this](const QString& theme) { themeChanged(theme); };
    connect(QGuiApplication::styleHints(), &QStyleHints::colorSchemeChanged, this, [this] {
        if (cfg().theme == u"system"_s)
            themeChanged(cfg().theme);
    });
    settings->changed = [this] { library->reload(); };
    pages = new QStackedWidget;
    const QList<std::pair<QString, Page*>> order = {
        {u"library"_s, library}, {u"organize"_s, organize}, {u"cfg"_s, cfgpage}, {u"bisect"_s, bisect},
        {u"dupes"_s, dupes}, {u"symlink"_s, symlink}, {u"settings"_s, settings}, {u"tray"_s, tray}};
    for (const auto& [key, w] : order) {
        pageMap.insert(key, w);
        pages->addWidget(w);
    }

    sidebar = new QTreeWidget;
    sidebar->setObjectName(u"sidebar"_s);
    sidebar->setHeaderHidden(true);
    sidebar->setColumnCount(2);
    sidebar->header()->setStretchLastSection(false);
    sidebar->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    sidebar->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    sidebar->setIndentation(14);
    sidebar->setRootIsDecorated(false);
    sidebar->setMinimumWidth(220);
    sidebar->setMaximumWidth(280);
    connect(sidebar, &QTreeWidget::itemClicked, this, [this](QTreeWidgetItem* wi, int) { sidebarClicked(wi); });
    connect(sidebar, &QTreeWidget::itemCollapsed, this, [this](QTreeWidgetItem* wi) {
        const int idx = wi->data(0, Qt::UserRole).toInt();
        if (idx >= 0 && idx < sideItems.size() && sideItems[idx].type.startsWith(u"header"_s))
            wi->setExpanded(true);
    });
    buildSidebar();

    auto* split = new QSplitter;
    split->addWidget(sidebar);
    split->addWidget(pages);
    split->setStretchFactor(1, 1);
    setCentralWidget(split);

    statusLabel_ = new QLabel;
    statusBar()->addWidget(statusLabel_, 1);
    pathLabel = new QLabel;
    pathLabel->setObjectName(u"muted"_s);
    statusBar()->addPermanentWidget(pathLabel);
    scanBar = new QProgressBar;
    scanBar->setTextVisible(false);
    scanBar->setFixedWidth(160);
    scanBar->hide();
    statusBar()->addPermanentWidget(scanBar);

    library->setViewMode(cfg().viewMode);
    viewAct->setText(cfg().viewMode == u"list"_s ? u"그리드 보기"_s : u"목록 보기"_s);
    library->setScope(u"전체"_s, QString(), QString(), INSTALLED);
    QTimer::singleShot(0, this, [this] { startup(); });
}

QAction* MainWindow::act(QToolBar* tb, const QString& text, std::function<void()> fn, const QKeySequence& shortcut, const QString& tip) {
    auto* a = new QAction(text, this);
    connect(a, &QAction::triggered, this, fn);
    if (!shortcut.isEmpty())
        a->setShortcut(shortcut);
    if (!tip.isEmpty())
        a->setToolTip(tip);
    tb->addAction(a);
    return a;
}

void MainWindow::startup() {
    const bool ok = !cfg().simsDir.isEmpty() && coreCall("paths.validate", {{"dir"_L1, cfg().simsDir}}).toArray()[0].toBool();
    if (!ok) {
        const QJsonArray found = coreCall("paths.find_sims_folders").toArray();
        if (found.size() == 1 && cfg().simsDir.isEmpty()) {
            setConfig({{"sims_dir"_L1, found[0].toObject()["root"_L1].toString()}});
        } else {
            SetupDialog d(this, cfg().modsDir);
            if (!d.exec() || d.resultDir.isEmpty()) {
                if (cfg().simsDir.isEmpty()) {
                    QMessageBox::information(this, u"S4CCM"_s, u"심즈4 모드 폴더를 지정해야 사용할 수 있습니다.\n설정에서 언제든 지정할 수 있습니다."_s);
                    goTo(u"settings"_s);
                    return;
                }
            } else {
                setConfig({{"sims_dir"_L1, d.resultDir}});
            }
        }
    }
    if (cfg().gameExe.isEmpty() || !isFile(cfg().gameExe)) {
        const QString exe = coreCall("paths.find_game_exe").toString();
        if (!exe.isEmpty())
            setConfig({{"game_exe"_L1, exe}});
    }
    saveConfig();
    simsDirChanged();
}

void MainWindow::simsDirChanged() {
    coreCall("lib.sims_dir_changed");
    pathLabel->setText(u"Mods: "_s + cfg().modsDir);
    settings->refreshPage();
    rescan(true);
}

void MainWindow::buildSidebar() {
    sidebar->clear();
    sideItems.clear();

    auto add = [this](QTreeWidgetItem* parent, const QString& text, Side data) {
        auto* it = parent ? new QTreeWidgetItem(parent, QStringList{text, QString()})
                          : new QTreeWidgetItem(sidebar, QStringList{text, QString()});
        it->setData(0, Qt::UserRole, int(sideItems.size()));
        it->setTextAlignment(1, Qt::AlignRight | Qt::AlignVCenter);
        data.item = it;
        sideItems << data;
        return it;
    };
    auto section = [&](const QString& text, const QString& type) {
        QTreeWidgetItem* it = add(nullptr, text, Side{type, {}, {}, {}, {}});
        it->setFlags(Qt::ItemIsEnabled);
        QFont f = it->font(0);
        f.setPointSizeF(8.5);
        f.setWeight(QFont::DemiBold);
        it->setFont(0, f);
        it->setSizeHint(0, QSize(0, 34));
        return it;
    };
    auto scope = [](const QString& group, const QString& category, const QString& status) {
        return Side{u"scope"_s, group, category, status, {}};
    };
    auto page = [](const QString& name) { return Side{u"page"_s, {}, {}, {}, name}; };

    QTreeWidgetItem* libRoot = section(u"라이브러리"_s, u"header"_s);
    QTreeWidgetItem* all = add(libRoot, u"전체"_s, scope({}, {}, INSTALLED));
    for (const auto& [g, glabel] : cats::GROUPS) {
        QTreeWidgetItem* gi = add(libRoot, glabel, scope(g, {}, INSTALLED));
        QList<cats::Category> subs;
        for (const auto& c : cats::CATEGORIES)
            if (c.group == g)
                subs << c;
        if (subs.size() > 1)
            for (const auto& c : subs)
                add(gi, c.label, scope({}, c.key, INSTALLED));
    }
    add(libRoot, u"비활성"_s, scope({}, {}, u"disabled"_s));
    add(libRoot, u"충돌 테스트로 잠시 뺀 것"_s, scope({}, {}, u"quarantine"_s));
    libRoot->setExpanded(true);
    QTreeWidgetItem* tools = section(u"도구"_s, u"header2"_s);
    add(tools, u"트레이"_s, page(u"tray"_s));
    add(tools, u"자동 정리"_s, page(u"organize"_s));
    add(tools, u"Resource.cfg"_s, page(u"cfg"_s));
    add(tools, u"충돌 테스트"_s, page(u"bisect"_s));
    add(tools, u"중복 검사"_s, page(u"dupes"_s));
    add(tools, u"심볼릭 링크"_s, page(u"symlink"_s));
    add(tools, u"설정"_s, page(u"settings"_s));
    tools->setExpanded(true);
    all->setSelected(true);
    paintSidebar();
}

void MainWindow::paintSidebar() {
    for (const Side& s : sideItems) {
        if (s.type.startsWith(u"header"_s))
            s.item->setForeground(0, QColor(tok("muted")));
        s.item->setForeground(1, QColor(tok("faint")));
    }
}

void MainWindow::themeChanged(const QString& theme) {
    applyTheme(theme);
    paintSidebar();
    library->reload();
}

void MainWindow::updateCounts() {
    const QList<ItemPtr>& items = library->model->items;
    QHash<QString, int> counts;
    auto keyOf = [](const QString& group, const QString& category, const QString& status) {
        return group + u'\x01' + category + u'\x01' + status;
    };
    int active = 0, unsorted = 0, scripts = 0, off = 0, testing = 0;
    for (const ItemPtr& it : items) {
        QStringList statuses{it->status};
        if (it->status == u"active"_s || it->status == u"disabled"_s)
            statuses << INSTALLED;
        for (const QString& st : statuses) {
            ++counts[keyOf({}, {}, st)];
            ++counts[keyOf(cats::groupOf(it->category), {}, st)];
            ++counts[keyOf({}, it->category, st)];
        }
        if (it->status == u"active"_s) {
            ++active;
            if (it->category == cats::UNSORTED)
                ++unsorted;
            if (it->kind == u"script"_s)
                ++scripts;
        } else if (it->status == u"disabled"_s) {
            ++off;
        } else if (it->status == u"quarantine"_s) {
            ++testing;
        }
    }
    for (const Side& s : sideItems) {
        if (s.type != u"scope"_s)
            continue;
        const int n = counts.value(keyOf(s.group, s.category, s.status), 0);
        const bool isAll = s.status == INSTALLED && s.group.isEmpty() && s.category.isEmpty();
        s.item->setText(1, (n || isAll) ? QString::number(n) : QString());
        if (!s.category.isEmpty() || s.status == u"quarantine"_s)
            s.item->setHidden(n == 0);
    }
    QString text = u"CC %1개 · 스크립트 모드 %2개 · 미분류 %3개 · 비활성 %4개"_s.arg(active - scripts).arg(scripts).arg(unsorted).arg(off);
    if (testing)
        text += u" · 충돌 테스트로 빼둔 것 %1개"_s.arg(testing);
    statusLabel_->setText(text);
}

void MainWindow::sidebarClicked(QTreeWidgetItem* wi) {
    const QVariant v = wi->data(0, Qt::UserRole);
    if (!v.isValid())
        return;
    const Side s = sideItems.value(v.toInt());
    if (s.type == u"scope"_s) {
        if (!goTo(u"library"_s))
            return;
        library->setScope(wi->text(0), s.group, s.category, s.status);
    } else if (s.type == u"page"_s) {
        goTo(s.page);
    }
}

bool MainWindow::goTo(const QString& page) {
    if (pages->currentWidget() == bisect && page != u"bisect"_s && !bisect->confirmLeave()) {
        for (const Side& s : sideItems)
            s.item->setSelected(s.type == u"page"_s && s.page == u"bisect"_s);
        return false;
    }
    Page* w = pageMap.value(page);
    pages->setCurrentWidget(w);
    if (w->hasRefresh() && page != u"library"_s)
        w->refreshPage();
    if (page != u"library"_s)
        for (const Side& s : sideItems)
            s.item->setSelected(s.type == u"page"_s && s.page == page);
    return true;
}

void MainWindow::refreshCurrentPage() {
    auto* cur = static_cast<Page*>(pages->currentWidget());
    if (cur != library && cur->hasRefresh())
        cur->refreshPage();
}

void MainWindow::rescan(bool show) {
    if (cfg().simsDir.isEmpty() || closing_)
        return;
    if (show) {
        try {
            runWithLoading(this, u"CC 목록을 읽는 중"_s, [](const LoadProgress& progress) {
                return coreCall("lib.scan", {}, phased(progress, u"파일을 하나씩 확인하고 있습니다"_s));
            }, Quips::Common);
        } catch (const CoreError& ex) {
            QMessageBox::warning(this, u"스캔 오류"_s, ex.msg);
        }
        lastScan_.restart();
        library->reload();
        refreshCurrentPage();
        return;
    }
    if (scan_.active) {
        rescanPending_ = true;
        return;
    }
    scanBar->show();
    scanBar->setValue(0);
    scanFailed_ = false;
    scan_.start(
        this,
        [this] {
            try {
                coreCall("lib.scan", {}, [this](int i, int n, const QString& text) {
                    postTo(this, [this, i, n, text] {
                        scanBar->setMaximum(qMax(n, 1));
                        scanBar->setValue(i);
                        statusLabel_->setText(u"스캔 중… %1/%2  %3"_s.arg(i).arg(n).arg(text.right(60)));
                    });
                });
            } catch (const CoreError& ex) {
                scanFailed_ = true;
                scanError_ = ex.msg;
            }
        },
        [this] { scanDone(); });
}

void MainWindow::scanDone() {
    scan_.join();
    lastScan_.restart();
    scanBar->hide();
    if (scanFailed_)
        QMessageBox::warning(this, u"스캔 오류"_s, scanError_);
    library->reload();
    refreshCurrentPage();
    if (rescanPending_) {
        rescanPending_ = false;
        QTimer::singleShot(0, this, [this] { rescan(); });
    }
}

void MainWindow::changeEvent(QEvent* e) {
    // 창으로 돌아올 때 자동으로 다시 스캔하지 않는다 (Python 판과 다름). 목록은 [다시 스캔]으로 직접 새로고침한다.
    QMainWindow::changeEvent(e);
}

void MainWindow::onSearch(const QString& text) {
    if (pages->currentWidget() != library)
        goTo(u"library"_s);
    library->setSearch(text);
}

void MainWindow::toggleView() {
    setConfig({{"view_mode"_L1, cfg().viewMode == u"grid"_s ? u"list"_s : u"grid"_s}}, true);
    library->setViewMode(cfg().viewMode);
    viewAct->setText(cfg().viewMode == u"list"_s ? u"그리드 보기"_s : u"목록 보기"_s);
}

void MainWindow::detailAction(const QString& action) {
    const QList<ItemPtr> items = library->selected();
    if (items.isEmpty())
        return;
    if (action == u"reveal"_s) {
        reveal(libAbs(items.first()->relPath));
    } else if (action == u"enable"_s || action == u"disable"_s) {
        setStatus(items, action == u"enable"_s ? u"active"_s : u"disabled"_s);
    } else if (action == u"trash"_s) {
        QStringList lines;
        for (const auto& i : items.mid(0, 10))
            lines << u"· "_s + i->name;
        const QString names = lines.join(u'\n') + (items.size() > 10 ? u"\n…"_s : QString());
        if (confirm(this, u"휴지통으로 삭제"_s, u"%1개를 휴지통으로 보낼까요?\n\n%2\n\n(휴지통에서 복원할 수 있습니다)"_s.arg(items.size()).arg(names))) {
            const QJsonObject res = coreCall("lib.trash", {{"ids"_L1, idsOf(items)}}).toObject();
            library->reload();
            QStringList failed;
            for (const auto& f : res["failed"_L1].toArray())
                failed << f.toString();
            if (!failed.isEmpty())
                QMessageBox::warning(this, u"휴지통으로 삭제"_s,
                                     u"휴지통으로 보내지 못한 항목이 있습니다. 게임이 켜져 있으면 끈 뒤 다시 해 보세요.\n\n"_s + failed.mid(0, 10).join(u'\n'));
        }
    }
}

void MainWindow::setStatus(const QList<ItemPtr>& items, const QString& to) {
    QList<ItemPtr> todo;
    for (const auto& i : items) {
        if (i->status == u"quarantine"_s) {
            QMessageBox::information(this, u"S4CCM"_s, u"충돌 테스트로 빠진 항목은 충돌 테스트 화면에서 원위치하세요."_s);
            return;
        }
    }
    for (const auto& i : items)
        if (i->status != to)
            todo << i;
    if (todo.isEmpty())
        return;
    int n = 0;
    {
        WaitCursor wait;
        n = coreCall("lib.set_status", {{"ids"_L1, idsOf(todo)}, {"to"_L1, to},
                                         {"label"_L1, (to == u"active"_s ? u"활성화"_s : u"비활성화"_s) + u" (%1개)"_s.arg(todo.size())}}).toInt();
    }
    library->reload();
    statusLabel_->setText(u"%1개를 %2 상태로 바꿨습니다."_s.arg(n).arg(statusLabel(to)));
}

void MainWindow::addFiles() {
    const QStringList files = QFileDialog::getOpenFileNames(this, u"추가할 모드 선택"_s, QString(), ADD_FILTER);
    if (!files.isEmpty())
        importPaths(files);
}

void MainWindow::addFolder() {
    const QString d = QFileDialog::getExistingDirectory(this, u"추가할 모드 폴더 선택"_s);
    if (!d.isEmpty())
        importPaths({d});
}

void MainWindow::importPaths(QStringList paths) {
    if (cfg().simsDir.isEmpty()) {
        QMessageBox::warning(this, u"모드 추가"_s, u"먼저 설정에서 심즈4 모드 폴더를 지정하세요."_s);
        return;
    }
    QJsonArray kept;
    for (const QString& p : paths)
        if (!coreCall("paths.overlaps", {{"sims_dir"_L1, cfg().simsDir}, {"path"_L1, p}}).toBool())
            kept << p;
    if (kept.isEmpty()) {
        QMessageBox::information(this, u"모드 추가"_s, u"이미 모드 폴더 안에 있는 파일입니다. [다시 스캔]을 누르세요."_s);
        return;
    }
    QJsonObject plan;
    try {
        plan = runWithLoading(this, u"추가할 파일을 살펴보는 중"_s, [kept](const LoadProgress& progress) {
            return coreCall("import.analyze", {{"paths"_L1, kept}}, stepped(progress));
        }, Quips::Import).toObject();
    } catch (const CoreError& ex) {
        QMessageBox::warning(this, u"모드 추가"_s, u"파일을 살펴보는 중 오류: "_s + ex.msg);
        return;
    }
    const QJsonValue handle = plan["handle"_L1];
    if (plan["groups"_L1].toArray().isEmpty()) {
        QStringList errors;
        for (const auto& e : plan["errors"_L1].toArray())
            errors << e.toString();
        QMessageBox::warning(this, u"모드 추가"_s, u"추가할 모드나 트레이 파일이 없습니다.\n\n"_s + errors.join(u'\n'));
        coreCall("import.cleanup", {{"handle"_L1, handle}});
        return;
    }
    ImportDialog dlg(plan, this);
    if (!dlg.exec()) {
        coreCall("import.cleanup", {{"handle"_L1, handle}});
        return;
    }
    QJsonObject res;
    const QJsonObject args{{"handle"_L1, handle}, {"groups"_L1, dlg.edits()}};
    try {
        res = runWithLoading(this, u"모드를 추가하는 중"_s, [args](const LoadProgress& progress) {
            return coreCall("import.execute", args, stepped(progress));
        }, Quips::Import).toObject();
    } catch (const CoreError& ex) {
        library->reload();   // 일부만 복사됐을 수 있다
        QMessageBox::warning(this, u"모드 추가"_s, u"추가 중 오류: "_s + ex.msg);
        return;
    }
    library->reload();
    const int mods = res["mods_added"_L1].toInt(), trays = res["trays_added"_L1].toInt();
    if (trays)
        tray->forgetLoaded();
    if (trays && !mods)
        goTo(u"tray"_s);
    else
        goTo(u"library"_s);
    QStringList bits;
    if (mods)
        bits << u"모드 파일 %1개"_s.arg(mods);
    if (trays)
        bits << u"트레이 항목 %1개"_s.arg(trays);
    statusLabel_->setText(bits.isEmpty() ? u"추가한 파일이 없습니다."_s : bits.join(u", "_s) + u"를 추가했습니다."_s);
    if (res["has_script"_L1].toBool())
        checkScriptOptions();
}

void MainWindow::checkScriptOptions() {
    const QString sims = cfg().simsDir;
    const QJsonObject opts = coreCall("rc.read_options", {{"sims"_L1, sims}}).toObject();
    if (opts.isEmpty())
        return;
    QStringList off;
    if (opts.value("modsdisabled"_L1).toString(u"0"_s) != u"0"_s)
        off << u"커스텀 콘텐츠와 모드 허용"_s;
    if (opts.value("scriptmodsenabled"_L1).toString(u"0"_s) != u"1"_s)
        off << u"스크립트 모드 허용"_s;
    if (off.isEmpty())
        return;
    if (!confirm(this, u"게임 옵션"_s, u"게임 옵션에서 다음 항목이 꺼져 있어 스크립트 모드가 작동하지 않습니다:\n\n· "_s
                                           + off.join(u"\n· "_s) + u"\n\n지금 켤까요? (게임이 꺼져 있어야 합니다)"_s))
        return;
    if (coreCall("launcher.running").toBool()) {
        QMessageBox::warning(this, u"게임 옵션"_s, u"게임이 실행 중입니다. 게임을 끈 뒤 Resource.cfg 화면에서 켜주세요."_s);
        return;
    }
    coreCall("rc.set_option", {{"sims"_L1, sims}, {"key"_L1, u"modsdisabled"_s}, {"value"_L1, u"0"_s}});
    coreCall("rc.set_option", {{"sims"_L1, sims}, {"key"_L1, u"scriptmodsenabled"_s}, {"value"_L1, u"1"_s}});
    statusLabel_->setText(u"게임 옵션에서 모드와 스크립트 모드를 켰습니다."_s);
}

void MainWindow::dragEnterEvent(QDragEnterEvent* e) {
    if (e->mimeData()->hasUrls())
        e->acceptProposedAction();
}

void MainWindow::dropEvent(QDropEvent* e) {
    QStringList files;
    for (const QUrl& u : e->mimeData()->urls())
        if (u.isLocalFile())
            files << u.toLocalFile();
    if (!files.isEmpty())
        QTimer::singleShot(0, this, [this, files] { importPaths(files); });
}

void MainWindow::closeEvent(QCloseEvent* e) {
    if (isBusy()) {
        e->ignore();
        QTimer::singleShot(300, this, [this] { close(); });
        return;
    }
    if (!bisect->confirmLeave(true)) {
        e->ignore();
        return;
    }
    library->detail->showItems({});
    closing_ = true;
    scan_.join();
    dupes->task.join();
    QMainWindow::closeEvent(e);
}

void MainWindow::selfTest(const QString& dir) {
    if (isBusy() || statusLabel_->text().isEmpty()) {   // 시작 스캔이 끝날 때까지 기다린다
        QTimer::singleShot(500, this, [this, dir] { selfTest(dir); });
        return;
    }
    QDir().mkpath(dir);
    auto shot = [&](const QString& name) {
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QApplication::processEvents();
        QApplication::processEvents();
        grab().save(dir + u'/' + name + u".png"_s);
    };
    shot(u"library_grid"_s);
    if (auto* grid = library->findChild<QListView*>(u"grid"_s))
        grid->setCurrentIndex(grid->model()->index(0, 0));
    shot(u"library_selected"_s);
    detailAction(u"disable"_s);
    shot(u"library_disabled"_s);
    library->setScope(u"비활성"_s, QString(), QString(), u"disabled"_s);
    if (auto* grid = library->findChild<QListView*>(u"grid"_s))
        grid->setCurrentIndex(grid->model()->index(0, 0));
    shot(u"scope_disabled"_s);
    detailAction(u"enable"_s);
    library->setScope(u"전체"_s, QString(), QString(), INSTALLED);
    search->setText(u"hair"_s);
    shot(u"search"_s);
    search->clear();
    library->setViewMode(u"list"_s);
    shot(u"library_list"_s);
    library->setViewMode(u"grid"_s);
    for (const QString& page : {u"tray"_s, u"organize"_s, u"cfg"_s, u"bisect"_s, u"dupes"_s, u"settings"_s}) {
        goTo(page);
        shot(page);
        if (page == u"tray"_s) {
            if (auto* list = tray->findChild<QListWidget*>(u"grid"_s))
                list->setCurrentRow(0);
            shot(u"tray_selected"_s);
        }
    }
    QApplication::quit();
}

void MainWindow::importSelfTest(const QString& dir, const QString& path) {
    if (isBusy() || statusLabel_->text().isEmpty()) {
        QTimer::singleShot(500, this, [this, dir, path] { importSelfTest(dir, path); });
        return;
    }
    QDir().mkpath(dir);
    auto* timer = new QTimer(this);
    auto shots = std::make_shared<int>(0);
    connect(timer, &QTimer::timeout, this, [this, dir, shots] {
        if (auto* d = dynamic_cast<ImportDialog*>(QApplication::activeModalWidget())) {
            d->grab().save(dir + u"/dialog.png"_s);
            for (QAbstractButton* b : d->findChild<QDialogButtonBox*>()->buttons())
                if (d->findChild<QDialogButtonBox*>()->buttonRole(b) == QDialogButtonBox::AcceptRole)
                    b->click();
            return;
        }
        if (isBusy() && *shots < 2000)
            grab().save(dir + u"/busy_%1.png"_s.arg(++*shots, 3, 10, QChar(u'0')));
    });
    timer->start(40);
    rescan(true);
    importPaths({path});
    timer->stop();
    grab().save(dir + u"/after.png"_s);
    QApplication::quit();
}

// 명령줄 인자 하나를 CommandLineToArgvW 규칙에 맞게 따옴표로 감싼다 (끝의 \ 는 따옴표를 깨뜨리므로 뗀다).
static QString quoteArg(QString a) {
    while (a.size() > 3 && (a.endsWith(u'\\') || a.endsWith(u'/')))
        a.chop(1);
    if (a.endsWith(u'\\'))   // "D:\" 같은 드라이브 루트
        a += u'.';
    return u'"' + a + u'"';
}

void MainWindow::elevateRelocate(const QString& dest) {
    QString params = u"--relocate "_s + quoteArg(dest);
    const QString home = qEnvironmentVariable("S4CCM_HOME");
    if (!home.isEmpty())
        params += u" --home "_s + quoteArg(home);
    const std::wstring file = QDir::toNativeSeparators(QCoreApplication::applicationFilePath()).toStdWString();
    const std::wstring args = params.toStdWString();
    // S4CCM_TEST_NO_RUNAS: 검증용. UAC 없이 같은 흐름을 일반 권한으로 시험한다.
    const std::wstring verb = qEnvironmentVariableIsSet("S4CCM_TEST_NO_RUNAS") ? L"open" : L"runas";
    SHELLEXECUTEINFOW info{};
    info.cbSize = sizeof(info);
    info.fMask = SEE_MASK_NOCLOSEPROCESS;
    info.hwnd = reinterpret_cast<HWND>(winId());
    info.lpVerb = verb.c_str();
    info.lpFile = file.c_str();
    info.lpParameters = args.c_str();
    info.nShow = SW_SHOWNORMAL;
    if (!ShellExecuteExW(&info) || !info.hProcess) {
        const DWORD err = GetLastError();
        if (err == ERROR_CANCELLED)
            QMessageBox::information(this, u"Mods 폴더 옮기기"_s, u"관리자 권한을 받지 못해 취소했습니다.\n아무것도 바뀌지 않았습니다."_s);
        else
            QMessageBox::warning(this, u"Mods 폴더 옮기기"_s, u"관리자 권한으로 다시 실행하지 못했습니다. (오류 %1)"_s.arg(err));
        return;
    }
    // 관리자 권한 S4CCM 이 일하는 동안 이 창은 숨겨 두었다가, 끝나면 다시 띄운다 (일반 권한으로 돌아온 것처럼 보인다).
    hide();
    const HANDLE process = info.hProcess;
    auto* poll = new QTimer(this);
    connect(poll, &QTimer::timeout, this, [this, poll, process] {
        if (WaitForSingleObject(process, 0) != WAIT_OBJECT_0)
            return;
        poll->stop();
        poll->deleteLater();
        DWORD code = 1;
        GetExitCodeProcess(process, &code);
        CloseHandle(process);
        show();
        raise();
        activateWindow();
        rescan(true);
        goTo(u"symlink"_s);
        statusLabel_->setText(code == 0 ? u"Mods 폴더를 옮겼습니다."_s : u"Mods 폴더를 옮기지 못했습니다."_s);
    });
    poll->start(200);
}

void MainWindow::resumeRelocate(const QString& dest) {
    if (isBusy() || statusLabel_->text().isEmpty()) {   // 시작 스캔이 끝날 때까지 기다린다
        QTimer::singleShot(300, this, [this, dest] { resumeRelocate(dest); });
        return;
    }
    goTo(u"symlink"_s);
    const bool ok = symlink->runRelocate(dest, true);
    QApplication::exit(ok ? 0 : 1);
}
