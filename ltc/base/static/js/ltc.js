function popitup(url) {
    let width = screen.width / 2;
    let height = screen.height / 2;
    newwindow=window.open(url,'{{title}}', `height=${height},width=${width}`);
    if (window.focus) {
        newwindow.focus()
    }
    return false;
}
