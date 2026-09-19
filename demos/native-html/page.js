'use strict';
// 小程序UI逻辑代码，此处无法调用酷家乐提供的接口！

function resize() {
    window.parent.postMessage({ type: 'resize' }, '*')
}

function fullscreen() {
    window.parent.postMessage({ type: 'fullscreen' }, '*')
}
function modal() {
    window.parent.postMessage({ type: 'modal' }, '*')
}

function position() {
    window.parent.postMessage({ type: 'position' }, '*')
}

function getUserName() {
    window.parent.postMessage({ type: 'getUserName' }, '*')
}

function send() {
    const input = document.getElementById('thing');
    window.parent.postMessage({ type: 'send', input: input.value }, '*'); // 发送消息至小程序VM代码
}

window.addEventListener('message', event => {
    if(event.data.action == 'getUserName'){
        document.getElementById('result1').innerText = event.data.value;
    }
    if(event.data.action == 'send'){
        document.getElementById('result2').innerText = event.data.value;
    }
    if(event.data.action == 'vmLog'){
        console.log('[vmLog]', event.data.value)
    }
});
