import asyncio, sounddevice, argparse
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent,TranscriptResultStream
from matplotlib import pyplot as plt
import boto3
from drawnow import drawnow

# transcription が送られてきたときのイベントハンドラークラス
class MyEventHandler(TranscriptResultStreamHandler):
    def __init__(self,TranscriptResultStream,language_code='ja'):
        super().__init__(TranscriptResultStream)
        self.transcription_list = ['']
        self.key_phrases_list = [[]]
        self.sentiment_list = {'Positive':[0.0],'Negative':[0.0],'Neutral':[0.0],'Mixed':[0.0]}
        self.new_flag = True
        self.start_time_list = [0]
        self.comprehend = boto3.client('comprehend')
        self.transcription = ''
        self.language_code=language_code
        self.detect_sentiment_return = {}
        self.text_display_param = {
            'row_num':1,
            'max_row_num':15,
            'fontsize':10,
        }
        self.fig = plt.figure(figsize=(16,9))
        self.counter=0
        self.display_interbal = 8 # 未確定センテンスの表示は重いので、8回に一回にする(マジックナンバー)
    
    def draw(self):
        plt.subplot(211).plot(self.start_time_list,self.sentiment_list['Positive'],color='g',label='Positive')
        plt.subplot(211).plot(self.start_time_list,self.sentiment_list['Negative'],color='r',label='Negative')
        plt.subplot(211).plot(self.start_time_list,self.sentiment_list['Neutral'],color='k',label='Newtral')
        plt.subplot(211).plot(self.start_time_list,self.sentiment_list['Mixed'],color='m',label='Mixed')
        plt.subplot(211).legend(bbox_to_anchor=(0, 1), loc='upper left', borderaxespad=0, fontsize=10)
        self.text_display_param['row_num'] = self.text_display_param['max_row_num'] if len(self.transcription_list) > self.text_display_param['max_row_num'] else len(self.transcription_list)
        plt.subplot(212).plot([0,1],[0,1],color='w')
        for i in range(self.text_display_param['row_num']):
            display_text = 'text : '
            display_text += self.transcription_list[-i-1]
            display_text += ', key phrases : '
            display_text += ','.join(self.key_phrases_list[-i-1])
            plt.subplot(212).text(
                0, # x 座標
                i*(1/self.text_display_param['max_row_num']), # y 座標
                display_text, # 表示する文言
                size=self.text_display_param['fontsize'],
            )




    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        # このイベントハンドラは、必要に応じて文字起こし結果を受け取った後の処理を実装
        results = transcript_event.transcript.results
        for result in results:
            if self.start_time_list[-1] < result.start_time:
                self.start_time_list.append(result.start_time)
                self.new_flag = True
            else:
                self.new_flag = False
            for alt in result.alternatives:
                self.transcription = alt.transcript.replace(' ','')
                if self.new_flag: # 確定処理
                    self.transcription_list.append(self.transcription)
                    self.detect_sentiment_return = self.comprehend.detect_sentiment(Text=self.transcription,LanguageCode=self.language_code)
                    self.detect_key_phrases_return = self.comprehend.detect_key_phrases(Text=self.transcription,LanguageCode=self.language_code)
                    # print(self.detect_key_phrases_return)
                    self.sentiment_list['Positive'].append(self.detect_sentiment_return['SentimentScore']['Positive'])
                    self.sentiment_list['Negative'].append(self.detect_sentiment_return['SentimentScore']['Negative'])
                    self.sentiment_list['Neutral'].append(self.detect_sentiment_return['SentimentScore']['Neutral'])
                    self.sentiment_list['Mixed'].append(self.detect_sentiment_return['SentimentScore']['Mixed'])
                    self.key_phrases_list.append([key_phrase['Text'] for key_phrase in self.detect_key_phrases_return['KeyPhrases']])
                    drawnow(self.draw)
                else: # 未確定処理
                    self.transcription_list[-1] = self.transcription
                    self.counter += 1
                    if self.counter == self.display_interbal:
                        self.detect_sentiment_return = self.comprehend.detect_sentiment(Text=self.transcription,LanguageCode=self.language_code)
                        self.detect_key_phrases_return = self.comprehend.detect_key_phrases(Text=self.transcription,LanguageCode=self.language_code)
                        self.sentiment_list['Positive'][-1] = self.detect_sentiment_return['SentimentScore']['Positive']
                        self.sentiment_list['Negative'][-1] = self.detect_sentiment_return['SentimentScore']['Negative']
                        self.sentiment_list['Neutral'][-1] = self.detect_sentiment_return['SentimentScore']['Neutral']
                        self.sentiment_list['Mixed'][-1] = self.detect_sentiment_return['SentimentScore']['Mixed']
                        self.key_phrases_list[-1] = [key_phrase['Text'] for key_phrase in self.detect_key_phrases_return['KeyPhrases']]
                        drawnow(self.draw)
                        self.counter = 0


async def mic_stream():
    # この関数は、マイクからの生の入力ストリームをラップし、ブロックをasyncio.Queueに転送します。
    loop = asyncio.get_event_loop()
    input_queue = asyncio.Queue()

    def callback(indata, frame_count, time_info, status):
        loop.call_soon_threadsafe(input_queue.put_nowait, (bytes(indata), status))

    # オーディオストリームのパラメータは、使用するソース言語で説明されているオーディオフォーマットに合ったものを使用してください。
    # https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html
    stream = sounddevice.RawInputStream(
        channels=1,
        samplerate=16000,
        callback=callback,
        blocksize=1024 * 2,
        dtype="int16",
    )
    # オーディオストリームを開始し、利用可能になると非同期にオーディオチャンクを生成します。
    with stream:
        while True:
            indata, status = await input_queue.get()
            yield indata, status


async def write_chunks(stream):
    # これは、マイクから送られてくる生のオーディオチャンクジェネレータを接続し、それを文字起こしストリームに渡します。
    async for chunk, status in mic_stream():
        await stream.input_stream.send_audio_event(audio_chunk=chunk)
    await stream.input_stream.end_stream()


async def basic_transcribe(args):
    # 選択したAWSリージョンでクライアントを設定する
    client = TranscribeStreamingClient(region=args.region)

    # 非同期ストリームを生成して文字起こしを開始します。
    stream = await client.start_stream_transcription(
        language_code=args.language_code,
        media_sample_rate_hz=args.media_sample_rate_hz,
        media_encoding=args.media_encoding,
    )

    # ハンドラのインスタンスを作成し、イベントの処理を開始します
    handler = MyEventHandler(stream.output_stream,language_code=args.language_code[0:2])
    await asyncio.gather(write_chunks(stream), handler.handle_events())

def arg_parse():
    parser = argparse.ArgumentParser()
    parser.add_argument('--region', type=str, default='ap-northeast-1')
    parser.add_argument('--language-code', type=str, default='ja-JP')
    parser.add_argument('--media-sample-rate-hz', type=int, default=16000)
    parser.add_argument('--media-encoding', type=str, default='pcm')
    args, _ = parser.parse_known_args()
    print(args)
    return args

if __name__ == "__main__":
    args = arg_parse()    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(basic_transcribe(args))
    loop.close()