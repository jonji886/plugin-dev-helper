const path = require('path');

const mode = process.env.NODE_ENV === 'production' ? 'production' : 'development';

module.exports = [
    {
        mode,
        target: 'web',
        entry: {
            main: ['./src/main.ts'],
            view: ['./src/view.tsx']
        },
        output: {
            path: path.resolve(__dirname, 'build'),
            filename: '[name].js'
        },
        resolve: {
            modules: ['node_modules'],
            extensions: ['.js', '.jsx', '.ts', '.tsx'],
        },
        devtool: mode === 'production' ? false : 'source-map',
        module: {
            rules: [
                {
                    test: /\.tsx?$/,
                    include: [
                        path.resolve(__dirname, 'src')
                    ],
                    exclude: [
                        /node_modules/,
                    ],
                    use: [
                        {
                            loader: 'ts-loader',
                        }
                    ]
                }
            ]
        }
    }
]
