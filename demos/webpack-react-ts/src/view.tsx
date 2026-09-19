// 页面逻辑代码，无法调用IDP等相关接口，但是可以使用react等ui框架或者库
import * as React from 'react';
import * as ReactDOM from 'react-dom';

interface State {
    userId: string;
    designId?: string;
}

class MyComponent extends React.PureComponent<{}, State> {
    state: State = {
        userId: '',
        designId: undefined
    };

    componentDidMount() {
        window.addEventListener('message', this.onmessage);
    }

    componentWillUnmount() {
        window.removeEventListener('message', this.onmessage);
    }

    private getDesignId = () => {
        window.parent.postMessage({ action: 'getDesignId' }, '*')
    }

    private getUserId = () => {
        window.parent.postMessage({ action: 'getUserId' }, '*')
    }

    private onmessage = (event: MessageEvent) => {
        if (event.data.action === 'userId') {
            this.setState({ userId: event.data.value });
        } else if (event.data.action === 'designId') {
            this.setState({ designId: event.data.value });
        }
    }

    render() {
        const { designId, userId } = this.state;
        return (
            <>
                <div>
                    <button onClick={this.getDesignId}>
                        getDesignId
                    </button>
                    <span style={{ marginLeft: "10px" }}>designId: {designId}</span>
                </div>
                <div style={{ marginTop: "10px" }}>
                    <button onClick={this.getUserId}>
                        getUserId
                    </button>
                    <span style={{ marginLeft: "10px" }}>userId: {userId}</span>
                </div>
            </>
        );
    }
}

ReactDOM.render(<MyComponent />, document.getElementById('react-container')!)
