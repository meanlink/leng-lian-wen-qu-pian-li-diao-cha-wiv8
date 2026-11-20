import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const sensitivity={
 list:()=>api('/sensitivity'),
 get:id=>api('/sensitivity/'+id),
 create:data=>api('/sensitivity',{method:'POST',body:JSON.stringify(data)}),
 retry:id=>api('/sensitivity/'+id+'/retry',{method:'POST'}),
 confirm:id=>api('/sensitivity/'+id+'/confirm',{method:'POST'}),
 briefs:id=>api('/sensitivity/'+id+'/briefs'),
 download:(bid,format)=>api('/sensitivity/briefs/'+bid+'/download?format='+format)
};
