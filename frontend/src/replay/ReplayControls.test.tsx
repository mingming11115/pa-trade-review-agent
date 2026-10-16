import { cleanup,fireEvent,render,screen } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { ReplayControls } from './ReplayControls';
import type { useReplaySession } from './useReplaySession';
afterEach(cleanup);
it.each(['1m','5m','1h'])('advances one %s candle without a previous-candle control',period=>{
 const command=vi.fn(),setPlaying=vi.fn();
 const replay={session:{mode:'training',state:{cursor:'2025-01-01T00:02:00Z'}},bars:[{timestamp:'2025-01-01T00:00:00Z'},{timestamp:'2025-01-01T00:01:00Z'}],period,command,setPlaying,canTrade:true,busy:false} as unknown as ReturnType<typeof useReplaySession>;
 render(<ReplayControls replay={replay}/>);
 expect(screen.queryByRole('button',{name:'上一根'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'下一分钟'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'下一根'}));
 expect(command).toHaveBeenCalledWith({type:'advance',period});
 expect(setPlaying).toHaveBeenCalledWith(false);
});

it('keeps compact playback accessible and includes exit controls without restoring removed controls',()=>{
 const command=vi.fn(),setPlaying=vi.fn(),leave=vi.fn();
 const replay={session:{mode:'training',state:{cursor:'2025-01-01T00:02:00Z'}},period:'5m',command,setPlaying,canTrade:true,busy:false,playing:false} as unknown as ReturnType<typeof useReplaySession>;
 render(<ReplayControls replay={replay} compact trailing={<button onClick={leave}>退出回放</button>}/>);
 const controls=screen.getByRole('group',{name:'回放控制'});
 expect(controls).toContainElement(screen.getByRole('button',{name:'退出回放'}));
 fireEvent.click(screen.getByRole('button',{name:'播放'}));
 expect(setPlaying).toHaveBeenCalledWith(true);
 fireEvent.click(screen.getByRole('button',{name:'下一根'}));
 expect(command).toHaveBeenCalledWith({type:'advance',period:'5m'});
 fireEvent.click(screen.getByRole('button',{name:'退出回放'}));
 expect(leave).toHaveBeenCalledOnce();
 expect(screen.queryByRole('button',{name:'上一根'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'定位'})).not.toBeInTheDocument();
 expect(screen.queryByText('速度')).not.toBeInTheDocument();
});
